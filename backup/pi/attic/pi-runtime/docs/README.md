# ER1 Pi Runtime Mirror

Source of truth for `/home/rudyy/er1` on the Pi.

- `scripts/` -> `/home/rudyy/er1/scripts/`
- `systemd/` -> `/etc/systemd/system/` (manual copy), working dir `/home/rudyy/er1`
- `config/local.env` -> `/home/rudyy/er1/config/local.env` (create from `local.env.example`, not committed)
- `logs/` -> `/home/rudyy/er1/logs/` (runtime only, not committed)

Workflow:
1) Deploy these files to the Pi under `/home/rudyy/er1`.
2) Copy `systemd/er1-mqtt-log.service` to `/etc/systemd/system/` and enable (`sudo systemctl daemon-reload && sudo systemctl enable --now er1-mqtt-log.service`).
3) Populate `/home/rudyy/er1/config/local.env` with broker/user overrides as needed.
