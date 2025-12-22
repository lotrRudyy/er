#!/bin/bash

BROKER="192.168.0.10"

publish() {
  mosquitto_pub -h "$BROKER" -t "$1" -m "$2" -q 1
  echo " → $1  <=  $2"
}

echo "=== DISABLING ALL ESCAPE-ROOM NODES ==="

publish "esc/ctrl/maglock/cmd"        "DISABLE"
publish "esc/room1/images-piano/cmd"  "DISABLE"
publish "esc/room2/chess/cmd"         "DISABLE"
publish "esc/room3/knocking/cmd"      "DISABLE"
publish "esc/room3/candles/cmd"       "DISABLE"
publish "esc/room3/star-sky/cmd"      "DISABLE"
publish "esc/room3/star-slider/cmd"   "DISABLE"
publish "esc/room3/stop-timer/cmd"    "DISABLE"

echo "=== ALL NODES SENT DISABLE ==="
echo "Now you can safely turn off the PSU (logic rail)."

