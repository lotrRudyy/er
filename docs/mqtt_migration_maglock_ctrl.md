MQTT migration: maglock_ctrl → canonical ER1 schema

Summary
-------
This note documents the current state prior to migration and the planned changes.

Current (pre-migration)
-----------------------
- maglock firmware binary: `maglock` (node id)
- maglock controller publishes metrics using a hard-coded topic and payload fields.
  - Metric topic (in code): `maglock/dbg` (see `src/ctrl/maglock_controller.cpp` const `kTopicMetric`).
  - Metric payload fields: uses `{"fw":...,"up":...,"k":"maglock_ctrl",...}` (non-`t` typed dbg payload).
- maglock subscribes to legacy lock commands via `maglock/lock/+/cmd` (see `src/maglock_main.cpp`).

Canonical (images_piano / ER1)
-----------------------------
- Canonical topic builder: `Core::topic(node, channel)` → `node/channel` (see `lib/core/src/core_mqtt.cpp`).
- Canonical node topics are created with `makeTopicConfig(nodeId)` and exposed in `NodeContext::config().topics`.
  - Heartbeat topic: `node/hb` with payload fields `{ "node":"...","fw":"...","build":"...","up":...,"ts":"...","time_valid":...,"health":"...","mem":"...","last_err":"..." }`
  - Debug/metric topic: `node/dbg` and payloads in this family include a `"t"` type field (see `src/riddles/images_riddle.cpp`).

Mismatch
--------
- `maglock_controller` uses a hard-coded metric topic constant and non-canonical payload keys (e.g., `k` instead of `t`), while other modules use `Core::topic(...)` and the `t`-typed dbg payload convention.

Plan
----
1. Add a deterministic selftest to show the current mismatch (will fail until migration).
2. Migrate `maglock_controller` to use `NodeContext` topics and canonical dbg payload fields.
3. Keep backward compatibility: continue subscribing to legacy `maglock/lock/+/cmd` while accepting canonical `maglock/cmd`.
4. Update this document after migration to mark completion and compatibility window.

References
----------
- Topic builder: `lib/core/src/core_mqtt.cpp` (`Core::topic`).
- Topic config: `lib/core/src/core_node.cpp` (`makeTopicConfig`).
- images_piano example: `src/images_piano_main.cpp` and `src/riddles/images_riddle.cpp`.
- maglock controller: `src/ctrl/maglock_controller.cpp` and `src/maglock_main.cpp`.
