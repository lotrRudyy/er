# ER1 Pi Configuration (er1_pi_config.md)

Authoritative location for all Raspberry Pi shell, environment, and usability configuration used in ER1.

---

## 1. Bash History Sync (Unified History Across All Terminals)

Ensures that local console, SSH sessions, and VS Code terminals share a single, live-updating command history.

### 1.1 .bashrc Snippet (Canonical)

Append to the bottom of `~/.bashrc`:

```bash
# --- Shared shell history (VS Code, SSH, local console) ---
export HISTFILE=~/.bash_history
export HISTSIZE=50000
export HISTFILESIZE=100000
shopt -s histappend

PROMPT_COMMAND='history -a; history -n'
```

### 1.2 How it works

* `history -a` → writes new commands immediately to the global history file.
* `history -n` → loads new commands other sessions added.
* Ensures Up-arrow always reflects all terminals.

---

## 2. Recommended Aliases & Quality-of-life Enhancements

Useful for daily operations on the Pi.

Add to `~/.bashrc`:

```bash
alias ll='ls -alF'
alias la='ls -A'
alias l='ls -CF'

# Quick jump to runtime
alias er='cd ~/er1'

# Tail live system log
alias er-log='sudo journalctl -fu er1-runtime.service'
```

---

## 3. Runtime Logging Helpers

```bash
# Live MQTT logs with timestamps
alias er-mqtt='~/er1/scripts/mqtt_logs.sh live'

# Lock events
alias er-locks='~/er1/scripts/mqtt_locks.sh'
```

---

## 4. Safety & Reliability Settings

Recommended for long-running Pi deployments.

Add/ensure in `/boot/firmware/cmdline.txt`:

```text
cgroup_enable=memory swapaccount=1
```

These enable better monitoring and container isolation (future use).

---

## 5. Useful System Commands

### 5.1 Check Ethernet stability

```bash
watch -n1 'ethtool eth0 | grep -E "Link detected|Speed"'
```

### 5.2 Restart ER1 runtime

```bash
sudo systemctl restart er1-runtime.service
```

### 5.3 Check runtime status

```bash
systemctl status er1-runtime.service
```

---

## 6. Distraction-free login message (optional)

Disable motd spam:

```bash
sudo chmod -x /etc/update-motd.d/*
```

---

## 7. Tailscale SSH

If Tailscale SSH enabled:

```bash
tailscale up --ssh
```

Then Pi is reachable without password from any trusted device.

---

## 8. VS Code Remote Settings

Recommended `settings.json` entries:

```json
{"remote.SSH.useLocalServer": false, "remote.SSH.lockfilesInTmp": true}
```

Improves stability on flaky connections.

---

## 9. TODO / Pending Enhancements

* Standardize Pi-side log rotation for MQTT logs
* Add automatic cleanup of old logfiles
* Add monitoring agents for disk usage and network outages
