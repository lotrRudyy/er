# ER1 Codex Report - 2025-12-05 08:45

## Markdownlint Scope + Fixes

- Scoped markdownlint to only `.md` files via `.markdownlint.json` (`default: true`, MD013/MD033/MD041 disabled, ignore all non-Markdown).
- Normalized Markdown spacing in README and docs (blank lines before lists/code fences, no content changes).
- Files touched: `.markdownlint.json`, `README.md`, `commands.md`, `docs/mqtt_commands.md`.

## Logging

### scripts/mqtt-logs.sh

- Rebuilt the script around the new daily logging model: only the local broker is subscribed, every message is stamped with `date +"[%d.%m.%Y %H:%M:%S.%3N]"`, and midnight rollover automatically switches to the next `logs/er1-DD.MM.YYYY.log`.
- Added `live`, `tail`, `grep`, and `help` subcommands so aliases can call the same entrypoint.
- Dropped the obsolete `scripts/logging/aliases_logging.sh`; `scripts/mqtt-logs.sh` is now the sole logging entrypoint.
- Snippet:

  ```bash
  mosquitto_sub -h "$LOCAL_BROKER" -t 'er1/#' -v \
    | ts "$TS_FORMAT" \
    | while IFS= read -r line; do
        today=$(date +%Y-%m-%d)
        ...
        cleaned=$(echo "$line" | sed -E "$SED_EXPR")
        echo "$cleaned" >> "$file"
        echo "$cleaned"
      done
  ```

### scripts/aliases.er1.sh

- Replaced ad-hoc `er_log_*` aliases with the final `log_live`, `log_tail`, `log_grep`, `log_help`, `lock_open`, and `lock_close` set.
- Added bash completion for `lock_open` / `lock_close` so valid IDs autocomplete.
- Snippet:

  ```bash
  alias log_live="scripts/mqtt-logs.sh live"
  alias lock_open="scripts/mqtt-locks.sh open"
  _er1_complete_locks() { COMPREPLY=( $(compgen -W "$_er1_lock_ids" -- "${COMP_WORDS[COMP_CWORD]}") ); }
  ```

### scripts/mqtt-locks.sh

- Simplified to a single `open|close <id> [remote]` interface with canonical lock ID validation.
- Brokers now default to local, with an explicit `remote` keyword for REMOTE_BROKER control only.
- Snippet:

  ```bash
  broker="$(select_broker "$scope")"
  require_lock_id "$id"
  send_cmd "${action^^}" "$id" "$broker"
  ```

## Documentation

### docs/mqtt_commands.md

- Rewritten as the authoritative cheatsheet: only lock control (local + optional remote), local live logging, log-to-file for Pi + Windows, and references to `scripts/mqtt-logs.sh` / `scripts/mqtt-locks.sh` / `scripts/aliases.er1.sh`.
- Snippet:

  ```markdown
  ./scripts/mqtt-logs.sh daemon  # writes to /home/rudyy/er1/logs/er1-DD.MM.YYYY.log with date+ms prefix
  ```

### commands.md

- Trimmed out per-device and remote logging examples so the file mirrors the new model (local logging only, remote broker strictly for control).

### ER1_PROTOCOL.md

- Updated the logging rules to codify the single pipeline, the Pi daily logfile, and the `log_live`/`log_tail`/`log_grep` aliases. Mentioned that remote broker use is for control only.
- Added the "ER1 Terminal Workflow (tmux session)" subsection describing how to run `scripts/er1-tmux.sh` and what each pane does.
- Snippet:

  ```markdown
  ./scripts/mqtt-logs.sh live  # date +"[%d.%m.%Y %H:%M:%S.%3N]" prefix
  ```

## Config

### .editorconfig

- Documented that ER1 deliberately stays on 2-space indentation across firmware + docs while still enforcing UTF-8, LF, final newline, and trimmed trailing whitespace.

### .markdownlint.json

- (No change beyond prior setup) Confirmed MD013 & MD033 remain disabled so the tooling matches ER1 docs.

### .vscode/extensions.json

- Removed the legacy comment block and left only the four required recommendations plus the single unwanted entry.

### .vscode/settings.json

- Added a minimalist marker (`"er1.settingsInfo": "..."`) to signal this file is now reserved for workspace-specific settings only.

### er.code-workspace

- Stripped the stray trailing comma and ensured the workspace only defines the repo folder plus the default PowerShell profile.

### .vscode/VsCodeTaskButtons.tasks

- Already mirrored `.vscode/tasks.json`; no content change required.

## Cleanup

- Removed the empty `er2/` and `er3/` directories plus the unused `.vscode/sessions.json` placeholder to keep the repo focused on ER1.
- Deleted the obsolete `scripts/logging/aliases_logging.sh` directory, consolidating all logging helpers behind `scripts/mqtt-logs.sh` and the new aliases.

## Protocol Update - 2025-12-04 22:12

### ER1_PROTOCOL.md

- Added the "No-Confirmation Execution (Mandatory)" subsection under 4.5 so Codex treats every prompt as full authorization.
- Renumbered the existing formatting and tmux subsections to 4.6 and 4.7 respectively; no other protocol sections were modified.
- Snippet:

  ```diff
   ### 4.4 Codex change application & confirmation
   ...
  -### 4.5 Formatting + Markdown linting
  +### 4.5 No-Confirmation Execution (Mandatory)
  +Codex must NEVER ask Rudy for confirmation before applying changes.
  +...
  +Rollbacks are handled via Git; Codex must not implement interactive approval flows.
  +
  +### 4.6 Formatting + Markdown linting
  ```
