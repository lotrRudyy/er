from __future__ import annotations

from models import GameMode

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
TOPIC_DEBUG_WILDCARD = "+/debug"

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
    "chains",
    "tangram",
    "magnet",
    "chess",
    "candles",
    "knocking",
    "star_sky",
    "star_slider",
]

MANUAL_RIDDLES = [
    "chains",
    "tangram",
    "magnet",
]

CONTROLLERS = ["lighting", "maglock"]
ALL_NODES = RIDDLES + CONTROLLERS

INITIAL_ACTIVE = ["images"]

# Progression:
# images -> piano -> chains
# chains -> tangram and magnet (both active in parallel)
# when BOTH tangram and magnet are solved -> chess
# chess -> candles and knocking
# candles -> star_sky and star_slider
UNLOCKS = {
    "images": ["piano"],
    "piano": ["chains"],
    "chains": ["tangram", "magnet"],
    "tangram": [],
    "magnet": [],
    "chess": ["candles", "knocking"],
    "candles": ["star_sky", "star_slider"],
    "knocking": [],
    "star_sky": [],
    "star_slider": [],
}

# Special progression gates that require a whole set to be solved before unlocking more riddles.
GATED_UNLOCKS = [
    {
        "requires_all": ["tangram", "magnet"],
        "unlock": ["chess"],
    }
]

DEFAULT_MODE = GameMode.MODE_STANDBY

INGAME_START_LIGHTS_ON = ["torch_stiege", "r1_bild", "r1_stuen"]
