#!/bin/bash

BROKER="192.168.0.10"

publish() {
  mosquitto_pub -h "$BROKER" -t "$1" -m "$2" -q 1
  echo " → $1  <=  $2"
}

echo "=== ENABLING ALL ESCAPE-ROOM NODES ==="

publish "esc/ctrl/maglock/cmd"        "ENABLE"
publish "esc/room1/images-piano/cmd"  "ENABLE"
publish "esc/room2/chess/cmd"         "ENABLE"
publish "esc/room3/knocking/cmd"      "ENABLE"
publish "esc/room3/candles/cmd"       "ENABLE"
publish "esc/room3/star-sky/cmd"      "ENABLE"
publish "esc/room3/star-slider/cmd"   "ENABLE"
publish "esc/room3/stop-timer/cmd"    "ENABLE"

echo "=== ALL NODES SENT ENABLE ==="
