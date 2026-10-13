# ER System Developer Workflow

This document describes the current ER1 development, runtime update, firmware
OTA, and maintenance workflow. The checked-out repository is the source of truth.

## 1. Repository Structure

```text
er/
  docs/                     # Protocol and operational documentation
  er1/
    firmware/               # PlatformIO firmware and OTA entrypoint
    data/                   # Local calibration and diagnostic data
    tools/                  # ER1 development tools
  scripts/
    er1_profile.ps1         # Developer command profile
    update_er1.py           # Safe Game Master/Dashboard updater
    game_master/            # Game Master runtime source
    mqtt_logs.sh            # MQTT logging utility
    ota_publish.py          # Pi-side OTA publisher source
  tests/                    # Python and JavaScript regression tests
  web/
    er1_dashboard/          # Dashboard runtime source
```

The production Pi is not a Git checkout. Runtime files are copied from a clean,
pushed local `main` checkout to `/home/rudyy/er1`.

## 2. Development Machine Setup

PowerShell 7, Git, Python 3, and SSH access to `rudyy@100.108.1.80` are required.
The PowerShell profile loads:

```powershell
. "$HOME\Documents\Escape Room\er\scripts\er1_profile.ps1"
```

Supported repo roots are:

- PC: `C:\Users\rudol\Documents\Escape Room\er`
- Laptop: `$HOME\Documents\er`

Run `er1 help` to list the profile commands. There is no `er1 deploy` command and
no active `er1/pi-runtime` tree.

## 3. Source Control

`er1 push "message"` stages, commits, and pushes from the repository root.
Review `git status` and the diff first because the command stages all changes.

Runtime updates accept only:

- branch `main`
- a clean working tree
- `HEAD` equal to its configured upstream
- a remote baseline marker that is an ancestor of `HEAD`

## 4. Game Master And Dashboard Updates

### 4.1 First-Time Baseline

The updater tracks the last verified source commit in
`/home/rudyy/er1/.deploy-source-commit`. Initialize it only when the deployed
managed files already match the current commit exactly:

The initial rollout of this updater is a one-time bootstrap exception: the new
Game Master `get_state` command and deployment-lock guard must first be copied by
the existing reviewed, file-scoped staging procedure while ER1 is idle. Restart
and verify both services, then initialize the marker. The updater intentionally
does not fall back to stale retained state or bypass its own guard to deploy that
guard. After this bootstrap, all later runtime changes use `er1 update`.

```powershell
er1 update -Initialize -DryRun
er1 update -Initialize
```

The dry run is mandatory operational practice. Initialization does not copy or
restart runtime files; it only writes the marker after exact hash and idle-state
verification.

### 4.2 Normal Update

```powershell
er1 update -DryRun
er1 update
```

The updater:

1. Requires a clean, pushed `main` checkout.
2. Confirms Dashboard `/api/state` is complete and healthy, then requests a fresh
   `game/dashboard_state` response from the Game Master. Both must report phase
   0, no timer, no live run, and healthy persistence.
3. Reads the remote source marker and refuses non-ancestor updates.
4. Compares every managed production file with its expected Git blob and stops
   on missing files or manual remote drift.
5. Computes only the managed runtime changes between the marker and `HEAD`.
6. Stages changed files under `/home/rudyy/er1/deploy-staging/`.
7. Verifies staged Git hashes, Python syntax, and JavaScript syntax.
8. Rechecks both state sources and creates a file-scoped rollback archive under
   `/home/rudyy/er1/deploy-backups/`.
9. Stops the Dashboard to close the normal operator start path, rechecks the
   fresh authoritative Game Master state, then stops the Game Master.
10. Installs or removes only the committed managed files and starts Game Master
    before the Dashboard.
11. Verifies both services, Dashboard HTTP routes, state, persistence, deployed
    hashes, deletions, and error-priority journals.
12. Advances the marker and removes staging only after all checks pass.

Actual updates use an exclusive remote lock so two development machines cannot
interleave deployments. While that lock exists, the Game Master rejects all
phase/start commands, including direct MQTT/profile commands. If post-restart
verification fails, rollback first stops
the Dashboard and proceeds only when authoritative Game Master state is idle. It
restores both files and the old source marker, restarts both services, and verifies
the old hashes and state. Unverifiable failures preserve staging and the rollback
archive instead of forcing another disruptive action.

### 4.3 Managed Files

Game Master mappings:

- `scripts/game_master/**/*.py` -> `/home/rudyy/er1/scripts/game_master/`
- `scripts/game_master/requirements.txt` -> the same remote component

Dashboard mappings:

- `web/er1_dashboard/app.py`, `log_archive.py`,
  `hint_templates.defaults.json`, and `requirements.txt` ->
  `/home/rudyy/er1/web/`
- tracked `static/` and `templates/` files -> matching directories below
  `/home/rudyy/er1/web/`

The updater deliberately excludes `.env`, databases, logs, hint/operator state,
virtual environments, backups, `.bak`/`.orig` files, firmware, and unrelated Pi
scripts. It never performs a whole-tree `rsync --delete`.

Changes to either `requirements.txt` are refused. Dependency or virtual-
environment migrations need a separate reviewed procedure because a file
rollback cannot safely undo installed packages.

### 4.4 Services And Health Endpoints

| Component | Source | Production path | Service |
| --- | --- | --- | --- |
| Game Master | `scripts/game_master/` | `/home/rudyy/er1/scripts/game_master/` | `game_master.service` |
| Dashboard | `web/er1_dashboard/` | `/home/rudyy/er1/web/` | `er1-web.service` |

The Dashboard listens on port `8080`. Read-only health routes are `/`, `/games`,
`/admin`, and `/api/state`.

## 5. Firmware OTA

Firmware OTA is separate from `er1 update`:

```powershell
er1 ota <target>
```

Examples:

```powershell
er1 ota images_piano
er1 ota maglock
```

The canonical target map lives in `er1/firmware/ota.ps1`. It builds the selected
PlatformIO environment, uploads the firmware to
`/home/rudyy/er1/node_firmware/`, publishes through
`/home/rudyy/er1/scripts/ota_publish.py`, and verifies the expected nodes.
Increment `FW_VERSION` for every firmware release.

## 6. Logs And Diagnostics

Common commands:

```powershell
er1 log
er1 log chess
er1 log -live
er1 log -errors
er1 log chess --save
er1 logs pretty
er1 mqtt status
```

Production MQTT logs are stored as
`/home/rudyy/er1/logs/er1-DD.MM.YYYY.log`. The active logging service is
`mqtt-log.service`. `scripts/mqtt_logs.sh` is not part of `er1 update`; changes
to the logger or its systemd unit require a separate reviewed deployment.

## 7. Troubleshooting

### `er1 update` says the marker is missing

Run `er1 update -Initialize -DryRun`. If hashes differ, do not initialize or
overwrite files. Resolve the reported drift first.

### Remote runtime drift

The updater prints every mismatching path with expected and actual Git hashes.
Determine whether the Pi file is an intentional hotfix before replacing it.

### Update lock already exists

Another update is running or a previous updater terminated unexpectedly. Verify
that no update process is active and inspect `/home/rudyy/er1/.update-lock/owner`
before manually removing the stale lock.

### Game is not idle

Wait until phase 0 with no timer and no run. Do not bypass the gate or restart
services during Preparation or Live.

### `requirements.txt` changed

Review the package change and plan the virtual-environment migration separately.
Do not force it through the file updater.

### Failed post-restart health check

Read the updater output. If it could prove idle state, it attempts file rollback
and service restart. Otherwise leave the game untouched and diagnose using the
retained staging directory, rollback archive, `systemctl status`, and
`journalctl`.

### OTA failure

Check the target name, built firmware, Pi firmware file, ESP connectivity, and
the OTA publisher output. Do not use `er1 update` for firmware.

## 8. Operating Rules

- Commit, review, test, and push before any runtime update.
- Always run `er1 update -DryRun` immediately before `er1 update`.
- Never update or restart while a game is active.
- Do not edit managed runtime files directly on the Pi.
- Never overwrite `.env`, databases, logs, or virtual environments during a
  source update.
- Keep firmware OTA and Python/web runtime updates separate.
- Preserve the printed rollback path in the release record.
