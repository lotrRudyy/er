# ER1 Protocol (Rudy × ChatGPT) v1.2.3

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

## 3. Logging Rules (date with milliseconds)

- All MQTT log capture must run through `scripts/mqtt-logs.sh` (no `ts`, no `awk` timestamping). The script stamps each line with `date +"[%d.%m.%Y %H:%M:%S.%3N]"`.
- Log lines must look like `[04.12.2025 05:39:29.924] esc/... {...}`.
- Logging is centralized on the Pi under `/home/rudyy/er1/logs/er1-DD.MM.YYYY.log`. `scripts/mqtt-logs.sh live` maintains the rolling logfile and also prints live output.
- To view logs, source `scripts/aliases.er1.sh` and use:
  - `log_live` to start the aggregator (LOCAL broker only).
  - `log_tail` to follow today's file.
  - `log_grep <pattern>` to grep today's file.
- Remote broker access is for control commands only; logging always uses the local broker.
- JSON in chat must always be compact inline, e.g. `{"lvl":"INFO","msg":"BTN idx=2 pin=14 state=RELEASED dt=30ms presses=288"}`.
- `er1/docs/mqtt_commands.md` holds the current lock and logging cheatsheet plus references to `scripts/mqtt-logs.sh` and `scripts/mqtt-locks.sh`.

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
- Large logs (>30-50 lines) need analysis.
- Any non-trivial change.

Codex is the default. Chat patching is the exception.

### 4.4 Codex change application & confirmation

Codex must not constantly ask for permission for individual edits once a prompt has been given.
- Rudy already approved single-confirmation workflow: when a command is clearly required but blocked by sandboxing, Codex must immediately retry it with the needed escalation instead of asking again.
- When requesting Rudy's confirmation, include a Markdown link to the updated `ER1_CODEX_REPORT.md` once so he can review details, and do not repeat the confirmation request unless he explicitly asks.

For each prompt, Codex should:

- Apply all changes that are clearly implied by the prompt and by this protocol (across all required files).
- Write a single, well-formatted report file in the repo, e.g. ER1_CODEX_REPORT.md (or update the existing one) that includes:
  - List of all modified files.
  - For each file: short summary of what changed.
  - Short before/after snippets for the most important edits.

In its chat response, Codex must explicitly tell Rudy:

- That the changes have been applied.
- That the report file was updated.
- That Rudy should review and confirm whether to keep these changes or revert them with git.

Codex must assume changes are tentative until Rudy confirms. It must not act as if they are "final" (e.g. by updating protocol references or telling future tools they are stable) until Rudy has explicitly confirmed.

### 4.5 No-Confirmation Execution (Mandatory)

Codex must NEVER ask Rudy for confirmation before applying changes.  
A Codex prompt is ALWAYS treated as full authorization.  
Codex must:

1. Apply ALL changes described in the prompt across ALL relevant files.
2. Write a full change log into ER1_CODEX_REPORT.md.
3. Provide a compact summary in chat.
4. Never pause execution to request human approval.

If ambiguity exists, Codex must choose the **most consistent, protocol-aligned interpretation**, apply it, and clearly document what it chose and why in ER1_CODEX_REPORT.md.

Rollbacks are handled via Git; Codex must not implement interactive approval flows.

### 4.6 Formatting + Markdown linting

- `.editorconfig` is the single source of truth for whitespace: LF endings, UTF-8, final newline, trailing whitespace trimming, and space indentation (2 spaces for source + docs, with overrides defined per file type). Keep the EditorConfig VS Code extension enabled so these rules apply automatically.
- Markdown is linted via `.markdownlint.json`. MD013 (line length) stays disabled so shell commands remain readable, and MD033 (inline HTML) stays off for flexibility. Install `DavidAnson.vscode-markdownlint` so every doc edit surfaces issues locally.
- If formatting rules need to change, update `.editorconfig` or `.markdownlint.json` instead of overriding settings locally, so the entire team stays aligned.

### 4.7 ER1 Terminal Workflow (tmux session)

- The standard Pi workflow is: `cd /home/rudyy/er1 && ./scripts/er1-tmux.sh`.
- If the `er1` tmux session already exists, the script simply attaches; otherwise it creates a three-pane layout (main shell, live MQTT logs, and a grep-ready shell with `scripts/mqtt-logs.sh grep ERROR` prefilled). Pane titles are `er1-main`, `er1-pi-log-mqtt`, and `er1-pi-log-grep`.
- tmux auto-starts `scripts/mqtt-logs.sh live` so the daily logfile is always running in the background. Use pane 3 or `log_grep` / `log_tail` for investigations without touching the live capture.

## Manual Pi Update (Option A)

deploy.sh is not tracked in Git.

It lives only on the Pi at:
`/home/rudyy/er1/deploy.sh`

Updates to the script must be done manually on the Pi.

The `.gitignore` rule prevents accidental commits.

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
  - mqtt_snapshot.json
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

**8.6.1 Tooling/UI labels for devices**
Any label, button text, or task name that directly represents a device (VS Code tasks, Task Button labels, OTA script targets, Node-RED control buttons, etc.) must use the canonical device ID string exactly (snake_case, as in the canonical list). No abbreviations or alternate spellings are allowed.

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

This file is ER1 Protocol v1.2.3.

- Any changes to workflow, naming, architecture, or tooling that are meant to be permanent must be added here.
- New versions must update the version number at the top.
