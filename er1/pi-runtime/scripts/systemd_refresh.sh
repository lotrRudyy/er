#!/usr/bin/env bash
set -euo pipefail

REMOTE_ROOT="/home/rudyy/er1"

# Install/refresh systemd unit files
sudo install -m 644 -T "$REMOTE_ROOT/systemd/ota-http.service" "/etc/systemd/system/ota-http.service"
sudo install -m 644 -T "$REMOTE_ROOT/systemd/ota-verify.service" "/etc/systemd/system/ota-verify.service"

# Ensure wrappers are executable
sudo chmod +x "$REMOTE_ROOT/scripts/ota-verify" 2>/dev/null || true
sudo chmod +x "$REMOTE_ROOT/scripts/ota" 2>/dev/null || true
sudo chmod +x "$REMOTE_ROOT/scripts/systemd_refresh.sh" 2>/dev/null || true

sudo systemctl daemon-reload
sudo systemctl enable ota-http.service ota-verify.service
sudo systemctl restart ota-http.service ota-verify.service
