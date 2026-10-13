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
- The dashboard can manually restart physical nodes other than maglock in every phase. Bulk and maglock restarts are blocked only during preparation or a live game (phases 2-13), and are allowed in standby (0), maintenance (1), and finished (14).
- Node restarts are operator-triggered only; the Game Master does not schedule periodic node restarts.

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
- Use the developer-profile commands `er1 lock <id> open|close` for lock control
  and `er1 log`/`er1 logs pretty` for log access.
- Logger or systemd changes require a separate reviewed deployment; `er1 update`
  intentionally manages only Game Master and Dashboard files.
