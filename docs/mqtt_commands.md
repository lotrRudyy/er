# MQTT Command Cheatsheet

Fast references for ER1 MQTT work. Constants:

- `LOCAL_BROKER = 127.0.0.1`
- `REMOTE_BROKER = 100.108.1.80`

Logging format: `[DD.MM.YYYY HH:MM:SS.mmm] topic payload` with `date +"[%d.%m.%Y %H:%M:%S.%3N]"`. Logfile pattern on Pi: `/home/rudyy/er1/logs/er1-DD.MM.YYYY.log`.

## OTA (secured)

- Payload: `UPDATE sha256=<64-hex> hmac=<64-hex> url=/firmware/<Dev>.bin`
- Host is pinned to `192.168.0.10`; paths must stay under `/firmware/`.
- `sha256` is the firmware hash; `hmac` is HMAC-SHA256 of that hash using `OTA_PSK` (computed automatically by `ota.ps1`).

## Lock Control

Lock IDs: `images`, `r2`, `r3`, `slider`, `knocking`.

### Local broker

```bash
mosquitto_pub -h 127.0.0.1 -t 'maglock/lock/<id>/cmd' -m "OPEN"
mosquitto_pub -h 127.0.0.1 -t 'maglock/lock/<id>/cmd' -m "CLOSE"
```

### Remote broker

```bash
mosquitto_pub -h 100.108.1.80 -t 'maglock/lock/<id>/cmd' -m "OPEN"
mosquitto_pub -h 100.108.1.80 -t 'maglock/lock/<id>/cmd' -m "CLOSE"
```

Replace `<id>` with one of the canonical lock IDs.

## Live Logging (LOCAL broker only)

```bash
./scripts/mqtt_logs.sh live
```

## Log-to-File Example (Pi)

```bash
./scripts/mqtt_logs.sh daemon
```

Each line is prefixed by `date +"[%d.%m.%Y %H:%M:%S.%3N]"` and written to `<deploy_root>/logs/er1-DD.MM.YYYY.log`.

## Scripts & Aliases

- Daily logging helpers live in `scripts/mqtt_logs.sh`. Use `daemon`, `live`, `tail`, or `grep` subcommands.
- Lock helpers live in `scripts/mqtt_locks.sh` with `open`/`close` actions.
- Source `scripts/aliases_er1.sh` to load the `log_*` and `lock_*` aliases (`log_live`, `log_tail`, `log_grep`, `log_help`, `lock_open`, `lock_close`).
