# ER1 Protocol (Rudy × ChatGPT) — v1.2.1

## 0. Purpose

This protocol defines how all development for the ER1 escape-room project is done:
- Firmware
- Logging
- Node-RED
- Shell scripts
- Configuration files
- Debug workflows
- Codex usage
- Version control consistency

Goal: stability, consistency, and speed.

---

## 1. Architecture Invariants (never break these)

Apply to every ESP32 node unless explicitly overridden:

- Transport: Ethernet-only with W5500 (no WiFi unless explicitly requested).
- Static IPs: must follow the ER1 IP plan.
- MQTT topics:  
  esc/<room>/<device>/{hb,event,cmd,log,metric}
- Device IDs (<device>):  
  Must be snake_case and consistent across:
  - Firmware
  - MQTT
  - OTA paths
  - Node-RED
  - Scripts
- OTA path:  
  /firmware/<dev>.bin
- Pattern:
  - FSM
  - Non-blocking (no delay hacks)
  - Heartbeat ~5s
  - Preferences restore FSM state
  - Retained heartbeat and retained critical state
  - Local fallback logic where required (e.g. verifyAll())

Maglocks may be controlled only by maglock_ctrl.  
Riddle nodes never drive maglocks directly.

If a sketch violates these rules, fix it and explicitly state what was corrected.

---

## 2. Versioning Rules

- Always start from the last confirmed working sketch unless a newer one is provided.
- Only change what Rudy explicitly asks for.
- Always bump FW_VERSION on every change.
- After Rudy confirms stability, always ask:  
  “Should I store this as the new working version?”

---

## 3. Logging Rules (ts with milliseconds)

All MQTT log commands must use:

    mosquitto_sub -h <IP> -t '<topic>' -v | ts '[%d.%m.%Y %H:%M:%.S]'

Log lines must look like:

    [04.12.2025 05:39:29.924] esc/... {...}

Any other timestamp style is incorrect.

JSON in chat must always be compact inline, for example:

    {"lvl":"INFO","msg":"BTN idx=2 pin=14 state=RELEASED dt=30ms presses=288"}

---

## 4. Code Editing — Codex-First Rule

All real code modifications must be done through Codex, not through large chat snippets.

ChatGPT must:

1. Generate a Codex prompt describing:
   - Goals
   - Files or areas to modify
   - Constraints
   - Expected results
2. Rudy pastes this prompt into Codex with the ER1 repo open.
3. Codex applies the changes directly to files.
4. Codex must output a report listing:
   - Modified files
   - Renamed files (if any)
   - Summary of changes
   - Short before/after snippets for the most important edits

### When chat patches are allowed

Only for micro-changes when ALL of these are true:
- Single line or very small local block.
- It is clearly faster than opening Codex.
- No cross-file effects.

In these cases, ChatGPT must:
- Provide a “Search” block: exact code to Ctrl+F.
- Provide a “Replacement” block: exact new code to paste.
- Never put explanations inside the code blocks.

### When Codex is always required

Codex is mandatory when:
- More than one file is affected.
- More than one area in a file is affected.
- Naming or identifier consistency work is needed.
- Any firmware restructuring or refactor.
- OTA/script updates.
- Node-RED flow changes.
- JSON structure changes.
- VS Code config changes.
- PlatformIO config changes.
- Large logs (>30–50 lines) need analysis.
- Any non-trivial change.

Codex is the default. Chat patching is the exception.

---

## 5. Post-Change Explanation

After any fix or change (whether via Codex or chat), ChatGPT must state:

1. What changed.
2. Why the bug happened (root cause, no fluff).
3. One prevention rule (short, actionable).

Example prevention format:

- “Prevention: always reset the press counter when entering STATE_IDLE.”

---

## 6. Memory Updating

ChatGPT must automatically store all new ER1 information Rudy gives, including:

- IPs and network layout.
- MQTT topics and patterns.
- OTA paths and device IDs.
- Architecture decisions.
- Confirmed working versions.
- Naming conventions.
- Debug insights and lessons learned.

Rudy does not need to say “save this”.  
Latest information always overrides older information.

---

## 7. Protocol Evolution

When ChatGPT sees a pattern that should be a rule, it must:

1. Propose exact text to add or change in this protocol.
2. Explicitly ask:  
   “Do you want to add this to the ER1 Protocol?”

No silent protocol changes.

---

## 8. Naming Conventions (strict)

To keep the entire ER1 project consistent:

### 8.1 Filenames and folders → snake_case

- Use lowercase with underscores.
- Examples:
  - restore_terminals.json
  - er1_logs
  - ota_script.ps1

Exceptions (leave as-is because they are standard):
- .vscode
- .git
- README.md
- platformio.ini

### 8.2 JSON keys → camelCase

- Example:
  - {"fwVersion":12,"pressCount":4,"lvl":"INFO"}

No snake_case or UPPER_SNAKE_CASE in JSON keys.

### 8.3 C++ constants → UPPER_SNAKE_CASE

- Examples:
  - BTN_PIN_1
  - HEARTBEAT_INTERVAL_MS
  - FW_VERSION

### 8.4 C++ variables and functions → camelCase

- Examples:
  - lastPressTs
  - handleButtons()
  - verifyAll()

### 8.5 MQTT topics

- Pattern:
  - esc/<room>/<device>/{hb,event,cmd,log,metric}

Examples:
- esc/room1/images_piano/hb
- esc/room3/star_sky/log
- esc/ctrl/lock/door_to_r3/state (lock IDs may contain underscores where appropriate)

### 8.6 Device IDs, MQTT <device> names, OTA <dev> → snake_case

Device IDs must:
- Use snake_case.
- Match across:
  - Firmware “Dev” name.
  - MQTT <device>.
  - OTA path basename (/firmware/<dev>.bin).

Canonical ER1 device IDs:

- maglock_ctrl
- images_piano
- chess
- knocking
- candles
- star_sky
- star_slider
- stop_timer

Invalid forms that must be eliminated:
- star-sky
- star-slider
- stop-timer
- images-piano
- MixedCase variants of the above.

Existing code using invalid forms must be migrated to these canonical IDs via Codex.

---

## 9. Project Scope

This protocol governs:

- All firmware for riddles.
- maglock_ctrl (maglock controller).
- Lighting controllers.
- Node-RED dashboard flows.
- Raspberry Pi scripts.
- Logging and diagnostics.
- Debug sessions and workflows.
- Git / version control practices (at a high level).
- Codex usage for editing.

---

## 10. Future Versions

This file is ER1 Protocol v1.2.1.

- Any changes to workflow, naming, architecture, or tooling that are meant to be permanent must be added here.
- New versions must update the version number at the top.
