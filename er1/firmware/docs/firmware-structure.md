# Firmware layout

- `lib/core/`: shared runtime (NodeCore, MQTT/Ethernet glue, OTA, logging). Put common services and helpers here so every feature uses the same source of truth instead of copying files.
- `lib/drivers/`: reusable hardware drivers that are puzzle-agnostic (e.g., maglock driver). Keep these focused on talking to hardware; orchestration lives above.
- `src/ctrl/`: controllers that wrap drivers and integrate them with the core (MQTT topics, timers, higher-level APIs). Reusable across riddles.
- `src/riddles/`: feature-based puzzle/orchestration modules (piano, images, chess, star slider, etc.). No room folders—name by what the feature does.
- `src/*.cpp`: entrypoints for PlatformIO environments. Each env pulls only the riddles/controllers it needs via `build_src_filter`.
- `include/`: intentionally empty for now; only use if you truly need a public header shared by multiple modules and not part of a library.

PlatformIO builds private libs from `lib/` automatically; each environment pulls only its own sources via `build_src_filter`.
