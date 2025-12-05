# Escape Room Repo Layout

- `er1/firmware/` — PlatformIO project (ESP32 + W5500, HTTP OTA, MQTT, FSM).
- `er1/pi-runtime/` — source of truth for `/home/rudyy/er1` on the Pi (scripts, systemd, config templates).
- `er1/docs/` — ER1-specific documentation (protocol, MQTT commands, PowerShell setup).
- `er2/`, `er3/` — placeholders for future rooms (firmware + pi-runtime skeletons).
- `shared/pc-scripts/` — Windows/macOS/Linux helper scripts for developers.
- `shared/libs/`, `shared/docs/` — reserved for shared code and documentation.

Canonical ER1 environments: room0_maglock_ctrl, room1_images_piano, room2_chess, room2_chess_rfid, room3_candles, room3_knocking, room3_star_sky, room3_star_slider, room3_stop_timer.
