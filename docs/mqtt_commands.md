# MQTT Command Cheatsheet

Fast references for ER1 MQTT work. Constants:

- `LOCAL_BROKER = 127.0.0.1`
- `REMOTE_BROKER = 100.108.1.80`

Logging format: `YYYY.MM.DD HH:MM:SS.mmm topic payload` with `date +"%Y.%m.%d %H:%M:%S.%3N"`. Logfile pattern on Pi: `/home/rudyy/er1/data/logs/er1-DD.MM.YYYY.log`.

## OTA (secured)

- Payload: `UPDATE {"id":"<nonce>","version":"<fw_version>","target":"<node_id>","url":"http://192.168.0.10/firmware/<FirmwareName>","sha256":"<64-hex>","size":<bytes>}`
- Topic: `<CmdNode>/cmd` (from the OTA map; e.g., images_piano publishes to `images/cmd`)
- Host is pinned to `192.168.0.10`; paths must stay under `/firmware/`. HTTPS is rejected.
- `sha256` is the firmware hash; `size` is optional but included by `ota.ps1`. Payloads are JSON; PSK/HMAC has been removed, so keep OTA on the trusted LAN.

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

Each line is prefixed by `date +"%Y.%m.%d %H:%M:%S.%3N"` and written to `<deploy_root>/data/logs/er1-DD.MM.YYYY.log`.

## Scripts & Aliases

- Daily logging helpers live in `scripts/mqtt_logs.sh`. Use `daemon`, `live`, `tail`, or `grep` subcommands.
- Lock helpers live in `scripts/mqtt_locks.sh` with `open`/`close` actions.
- Source `scripts/aliases_er1.sh` to load the `log_*` and `lock_*` aliases (`log_live`, `log_tail`, `log_grep`, `log_help`, `lock_open`, `lock_close`).
