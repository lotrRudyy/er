# MQTT Topic Scheme

- Format: `<node>/<channel>` (no prefixes, no room ids, no leading slash).
- Channels:
  - `cmd`   – inbound commands for a node.
  - `hb`    – heartbeats / presence.
  - `evt`   – discrete events.
  - `state` – retained state snapshots.
  - `dbg`   – optional debug/metrics (opt-in in log captures).
  - `log`   – structured logs.
  - `cfg`   – configuration payloads (when used).
- Nodes are the device identities (e.g., `maglock`, `images_piano`, `piano`, `chess`, `stop_timer`, `star_slider`, `star_sky`, `knocking`, `candles`).
- Images/Piano split: firmware exposes two logical nodes on one MCU: `images_piano/<channel>` and `piano/<channel>`.
- Timestamps: when present, wall-clock timestamps use `ts` formatted as `YYYY-MM-DD HH:MM:SS.mmm` (same format emitted by the core time formatter).
