# ER1 Logging and Tools

## Path layout (Pi)
- Repo root: `/home/rudyy/er1`
- Scripts: `/home/rudyy/er1/scripts` (source: `er1/pi-runtime/scripts/`)
- Logs: `/home/rudyy/er1/logs/er1-DD.MM.YYYY.log` (runtime only)
- Docs: `/home/rudyy/er1/docs` (runtime help; source: `er1/pi-runtime/docs/`)
- systemd reference: `/home/rudyy/er1/systemd/er1-mqtt-log.service` (source: `er1/pi-runtime/systemd/`, copy to `/etc/systemd/system/` manually)

## MQTT logging (`scripts/mqtt_logs.sh`)
- Broker: `LOCAL_BROKER` env (defaults to `127.0.0.1`).
- Timestamp: `date +"[%d.%m.%Y %H:%M:%S.%3N]"`.
- Subcommands:
  - `daemon` — write er1/# to the daily log (no stdout).
  - `live` — same as daemon but also echoes to the terminal.
  - `tail` — `tail -f` today's file.
  - `grep <pattern>` — grep today's file.
- Log rotation: filename rolls at midnight to `er1-DD.MM.YYYY.log`.

## Live logging wrapper (`scripts/log_live.sh`)
- Convenience wrapper for interactive sessions:
  - `./scripts/log_live.sh` (cds to `/home/rudyy/er1` then runs `./scripts/mqtt_logs.sh live`).

## OTA helper (`scripts/ota`)
- Usage: `./scripts/ota <target>`
- Device map:
  - `maglock_ctrl` → Env `room0_maglock_ctrl`, Dev `maglock_ctrl`
  - `images_piano` → Env `room1_images_piano`, Dev `images_piano`
  - `chess` → Env `room2_chess`, Dev `chess`
  - `knocking` → Env `room3_knocking`, Dev `knocking`
  - `candles` → Env `room3_candles`, Dev `candles`
  - `star_sky` → Env `room3_star_sky`, Dev `star_sky`
  - `star_slider` → Env `room3_star_slider`, Dev `star_slider`
  - `stop_timer` → Env `room3_stop_timer`, Dev `stop_timer`
- Requires `pwsh`. If missing on the Pi, run the OTA from Windows against the firmware project (`er1/firmware/ota.ps1`):
  - `pwsh -NoLogo -File ./ota.ps1 -Env "<Env>" -Dev "<Dev>"`

## Systemd (reference only)
- Repo includes `systemd/er1-mqtt-log.service`.
- To enable on the Pi: copy it to `/etc/systemd/system/`, then `sudo systemctl daemon-reload`, `sudo systemctl enable --now er1-mqtt-log.service`.
