# MQTT Command Cheatsheet

Fast references for ER1 MQTT work. Constants:

- `LOCAL_BROKER = 127.0.0.1`
- `REMOTE_BROKER = 100.108.1.80`

Logging format: `YYYY.MM.DD HH:MM:SS.mmm topic payload` with `date +"%Y.%m.%d %H:%M:%S.%3N"`. Logfile pattern on Pi: `/home/rudyy/er1/logs/er1-DD.MM.YYYY.log`.

## OTA (secured)

- Payload: `UPDATE {"version":"<fw_version>","target":"<node_id>","url":"http://192.168.0.10/node_firmware/<FirmwareName>","sha256":"<64-hex>","size":<bytes>}`
- Topic: `<CmdNode>/sys/cmd` (from the OTA map; e.g., images_piano publishes to `images_piano/sys/cmd`)
- Host is pinned to `192.168.0.10`; paths must stay under `/node_firmware/`. HTTPS is rejected.
- `sha256` is the firmware hash; `size` is optional but included by `ota.ps1`. Payloads are JSON; PSK/HMAC has been removed, so keep OTA on the trusted LAN.

## Lock Control

Lock IDs: `images`, `r2`, `r3`, `slider`, `knocking`.

### Local broker

```bash
mosquitto_pub -h 127.0.0.1 -t 'maglock/cmd' -m '{"cmd":"open","lock":"<id>"}'
mosquitto_pub -h 127.0.0.1 -t 'maglock/cmd' -m '{"cmd":"close","lock":"<id>"}'
```

### Remote broker

```bash
mosquitto_pub -h 100.108.1.80 -t 'maglock/cmd' -m '{"cmd":"open","lock":"<id>"}'
mosquitto_pub -h 100.108.1.80 -t 'maglock/cmd' -m '{"cmd":"close","lock":"<id>"}'
```

Replace `<id>` with one of the canonical lock IDs.

## Node restart

- Topic: `<node>/sys/cmd`
- Payload: `REBOOT`
- The command invokes `ESP.restart()` through the shared firmware core; it does not start OTA.
- After operator confirmation, the dashboard can restart individual puzzle nodes in active phases. Restarting all nodes or the maglock controller is restricted to phase 0.
- The Game Master schedules the all-node restart every 14 days at 04:00 Europe/Rome, deferring a due restart until phase 0.

```bash
mosquitto_pub -h 127.0.0.1 -t 'images_piano/sys/cmd' -m 'REBOOT'
```

## Live Logging (LOCAL broker only)

```bash
./scripts/mqtt_logs.sh live
# Pretty dashboard with OTA merge + hb cache:
./scripts/mqtt_logs.sh pretty
```

## Log-to-File Example (Pi)

```bash
./scripts/mqtt_logs.sh daemon
```

Each line is prefixed by `date +"%Y.%m.%d %H:%M:%S.%3N"` and written to `<deploy_root>/logs/er1-DD.MM.YYYY.log`.
Raw logging excludes `time/state` by default; the pretty view subscribes to it for the dashboard.

## Scripts & Aliases

- Daily logging helpers live in `scripts/mqtt_logs.sh`. Use `daemon`, `live`, `tail`, or `grep` subcommands.
  - `pretty` renders the human dashboard (hb/time + OTA merge).
- Lock helpers live in `scripts/mqtt_locks.sh` with `open`/`close` actions.
- Source `scripts/aliases_er1.sh` to load the `log_*` and `lock_*` aliases (`log_live`, `log_tail`, `log_grep`, `log_help`, `lock_open`, `lock_close`).
- For Pi-side file refresh (log path now `/home/rudyy/er1/logs/`), see the manual steps in `docs/workflow.md#manual-update-on-pi-mqtt-logging-runtime-files`.
