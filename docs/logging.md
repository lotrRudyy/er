# Logging & Heartbeats

## Heartbeat payload (20s, retained)

Published on `<node>/hb` every 20s and immediately on MQTT connect. Required fields:

- `node`, `fw`, `build`, `up`
- `ts`, `time_valid`
- `heap_free`, `heap_min`, `heap_largest`, `heap_size` (bytes)
- `err_cnt`, `err_code`, `err_since_up` (optional), `err_msg` (only when `err_code != 0`)

Example:

```json
{
  "node": "chess",
  "fw": "1.7",
  "build": "ABC123",
  "up": 9421,
  "ts": "2025.12.23 11:27:41.000",
  "time_valid": true,
  "heap_free": 183456,
  "heap_min": 172000,
  "heap_largest": 120000,
  "heap_size": 311296,
  "err_cnt": 0,
  "err_code": 0
}
```

## Raw logger (machine parsable)

`./scripts/mqtt_logs.sh daemon|live` captures `+/log`, `+/hb`, `+/evt`, `+/state` to `logs/er1-DD.MM.YYYY.log` with `date +"%Y.%m.%d %H:%M:%S.%3N"` prefix. `time/state` is excluded from the raw stream to keep the files quiet. Set `INCLUDE_DBG=1` to add `+/dbg`.

## Pretty logger (`er1 logs pretty`)

`./scripts/mqtt_logs.sh pretty` (or `er1 logs pretty` on the Pi) renders a human view:

- Minute-aligned dashboard blocks with the latest `time/state` and heartbeats, sorted by uptime.
- Flags `(RESTARTED)` on uptime drops, `(STALE)` if no hb for >40s.
- Warns only when time is invalid or `time/state` is stale (>90s).
- OTA events are merged per node/id (START/PROGRESS/FLASHED/OK/FAIL + reboot detection).
- Multiline logs (e.g., chess table) are printed as one message.
- Requires `paho-mqtt` on the Pi (`pip install paho-mqtt`).

## Chess table log (single publish)

When RFID state changes, chess emits one multiline log:

```
chess/log INF 11:11:14.637 - 2025.12.23
---------------------------------------------------------------------------
Reader 1 (rst=32 cs=14) | GOAL: QUEEN | NOW : QUEEN
Reader 2 (rst=33 cs=13) | GOAL: HORSE | NOW : HORSE
Reader 3 (rst=25 cs=17) | GOAL: ROOK  | NOW : ROOK
Reader 4 (rst=26 cs=16) | GOAL: KING  | NOW : KING
```

The pretty logger preserves the multiline body as-is.
