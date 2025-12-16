#!/usr/bin/env bash
set -euo pipefail

REMOTE_ROOT="/home/rudyy/er1"

# Install/refresh systemd unit files
sudo install -m 644 -T "$REMOTE_ROOT/systemd/ota-http.service" "/etc/systemd/system/ota-http.service"
sudo install -m 644 -T "$REMOTE_ROOT/systemd/ota-verify.service" "/etc/systemd/system/ota-verify.service"

# Mirror OTA entrypoints into node_firmware/
sudo mkdir -p "$REMOTE_ROOT/node_firmware"
sudo install -m 755 "$REMOTE_ROOT/scripts/ota_http.py" "$REMOTE_ROOT/node_firmware/ota_http.py"
sudo install -m 755 "$REMOTE_ROOT/scripts/ota_verify.py" "$REMOTE_ROOT/node_firmware/ota_verify.py"
sudo install -m 755 "$REMOTE_ROOT/scripts/ota_publish.py" "$REMOTE_ROOT/node_firmware/ota_publish.py"

# Ensure service entrypoints are executable
sudo chmod +x "$REMOTE_ROOT/node_firmware/ota_verify.py" 2>/dev/null || true
sudo chmod +x "$REMOTE_ROOT/node_firmware/ota_http.py" 2>/dev/null || true
sudo chmod +x "$REMOTE_ROOT/scripts/systemd_refresh.sh" 2>/dev/null || true

sudo systemctl daemon-reload
sudo systemctl enable ota-http.service ota-verify.service
sudo systemctl restart ota-http.service ota-verify.service
