from __future__ import annotations

from pathlib import Path

MQTT_HOST = "127.0.0.1"
MQTT_PORT = 1883
MQTT_KEEPALIVE = 30

TOPIC_GAME_STATE = "game/state"
TOPIC_DASHBOARD_STATE = "game/dashboard_state"
TOPIC_GAME_EVENT = "game/event"
TOPIC_GAME_CMD = "game/cmd"
TOPIC_LIGHTING_CMD = "lighting/cmd"
TOPIC_MAGLOCK_CMD = "maglock/cmd"
TOPIC_GAME_MASTER_DEBUG = "game_master/debug"

TOPIC_HB_WILDCARD = "+/hb"
TOPIC_NODE_STATE_WILDCARD = "+/state"

HEARTBEAT_TIMEOUT_S = 15.0
SCHEDULER_TICK_MS = 200

GAME_MASTER_DIR = Path(__file__).resolve().parent
DATA_DIR = GAME_MASTER_DIR / "data"
DB_PATH = DATA_DIR / "game_master.sqlite3"
RUNS_DIR = DATA_DIR / "game_runs"
ACTIVE_RUN_CHECKPOINT_PATH = DATA_DIR / "active_run_checkpoint.json"
ACTIVE_RUN_CHECKPOINT_INTERVAL_S = 5.0

RIDDLES = [
    "images",
    "piano",
    "prison",
    "wheel",
    "chains",
    "tangram",
    "magnet",
    "chess",
    "knocking",
    "candles",
    "stars",
    "sissi",
]

MANUAL_RIDDLES = [
    "prison",
    "wheel",
    "chains",
    "tangram",
    "magnet",
    "sissi",
]

CONTROLLERS = ["lighting", "maglock"]
ALL_NODES = RIDDLES + CONTROLLERS

DEFAULT_PHASE = 0
