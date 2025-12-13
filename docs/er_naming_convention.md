# Escape Room Repo Naming Convention

This document defines the official naming conventions for the **Escape Room** repository (all projects: `er1`, future rooms, shared tooling, docs, and infrastructure). The goal is consistency, clarity, and friction-free use across PowerShell, Python, Linux, CI, and embedded systems.

---

## 1. General Rules

* Use **lowercase only**
* No spaces in names
* Prefer clarity over brevity
* Follow the conventions below strictly

```
good:  er_profile.ps1
bad:   ER-Profile FINAL.ps1
```

---

## 2. Directories

**Style:** `kebab-case`

Use kebab-case for all folders at every level.

```
docs/
pc-scripts/
pi-runtime/
firmware/
web/
er1/
er2/
room1/
room2/
docs/er/
```

Reason: folders are frequently typed and visually scanned; kebab-case is easiest to read.

---

## 3. Script Files

### PowerShell (`.ps1`)

**Style:** `snake_case`

* Verb-first if the script performs an action
* Nouns allowed for profiles or libraries
* Prefer **repo-scoped** prefixes when a script is shared

```
er_profile.ps1
codex_commit.ps1
deploy_firmware.ps1
ota_verify.ps1
```

If a script is project-specific, include the project/room name:

```
er1_profile.ps1
room1_piano_calibrate.ps1
```

---

### Python (`.py`)

**Style:** `snake_case`

* File name should match the primary responsibility
* Prefer repo-scoped prefixes for shared utilities

```
ota_verify.py
mqtt_bridge.py
piano_mapper.py
```

Project/room-specific examples:

```
er1_ota_verify.py
room1_piano_mapper.py
```

---

## 4. CLI / Executable Names

**Style:** `kebab-case`

Used in documentation, commands, and task runners.

```
ota-verify
codex-commit
piano-calibrate
```

> The backing file may still be `snake_case` (e.g. `ota_verify.py`).

If ambiguity exists across rooms/projects, prefix the command:

```
er1-ota-verify
room1-piano-calibrate
```

---

## 5. Configuration Files

**Style:** `snake_case`

```
er_config.yaml
mqtt_config.json
room1_piano_map.yaml
```

Always keep the file extension explicit.

---

## 6. Environment Variables

**Style:** `UPPERCASE_SNAKE_CASE`

Repo-wide variables:

```
ER_ENV=prod
MQTT_BROKER_HOST=localhost
PI_RUNTIME_PATH=/opt/er
```

Project/room-specific variables:

```
ER1_ENV=prod
ROOM1_PIANO_DEVICE_ID=...
```

---

## 7. Git Conventions

### Branch Names

**Style:** `kebab-case`

Use a prefix for scope when helpful:

```
feature/room1-piano-calibration
fix/er1-mqtt-timeout
chore/repo-naming-doc
```

### Commit Messages

* Imperative mood
* Short and descriptive
* Include scope when needed

```
add room1 piano frequency calibration
fix er1 ota verify mqtt dependency
```

---

## 8. Hardware / Room Identifiers

**Style:** fixed prefix + number

```
room1
room2
room1_piano
room1_door
```

---

## 9. Disallowed Patterns

❌ Mixed casing
❌ Spaces in names
❌ Version numbers in filenames (`script_v3_final.ps1`)
❌ Renaming files instead of using git history

---

**This document is authoritative.** New files and folders must conform before merge.
