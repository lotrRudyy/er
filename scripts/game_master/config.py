from __future__ import annotations

MQTT_HOST = "127.0.0.1"
MQTT_PORT = 1883
MQTT_KEEPALIVE = 30

TOPIC_GAME_STATE = "game/state"
TOPIC_GAME_EVENT = "game/event"
TOPIC_GAME_CMD = "game/cmd"
TOPIC_LIGHTING_CMD = "lighting/cmd"
TOPIC_MAGLOCK_CMD = "maglock/cmd"
TOPIC_GAME_MASTER_DEBUG = "game_master/debug"

TOPIC_HB_WILDCARD = "+/hb"
TOPIC_NODE_STATE_WILDCARD = "+/state"

HEARTBEAT_TIMEOUT_S = 15.0
SCHEDULER_TICK_MS = 200

# These are relative to the directory where main.py is started.
# On your Pi, if you run from ~/er1/scripts/game_master, they become:
#   ~/er1/scripts/game_master/data/game_master.sqlite3
#   ~/er1/scripts/game_master/data/game_runs/
DB_PATH = "data/game_master.sqlite3"
RUNS_DIR = "data/game_runs"

RIDDLES = [
    "images",
    "piano",
    "open_prison",
    "mount_wheel",
    "rope_paths",
    "tangram",
    "magnet",
    "chess",
    "knocking",
    "candles",
    "star_slider",
    "sissi",
]

MANUAL_RIDDLES = [
    "open_prison",
    "mount_wheel",
    "rope_paths",
    "tangram",
    "magnet",
    "sissi",
]

CONTROLLERS = ["lighting", "maglock"]
ALL_NODES = RIDDLES + CONTROLLERS

DEFAULT_PHASE = 1
