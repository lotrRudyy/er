Pi Provisioning

This document is the one-time provisioning procedure for Raspberry Pi nodes.
Run this once per Pi image (fresh OS install / reflashed SD). Deploys should not install OS dependencies.

Scope

Installs OS packages and Python dependencies required by er1/pi-runtime services.

Enables systemd services shipped in the repo.

Validates the runtime environment.

Assumptions

Pi OS (Debian-based), user has sudo.

Repo is deployed to a known path (example: /opt/escape-room/er).

er1/pi-runtime/systemd/*.service exists and is the source of truth for what runs.

If any of these are wrong, fix this document to match your repo.

1) Base OS setup
sudo apt update
sudo apt -y full-upgrade
sudo apt -y install git rsync curl ca-certificates jq
sudo reboot

2) Python runtime dependencies
Required for ota-verify.py

ota-verify.py imports paho.mqtt.client, so install paho for the system Python:

sudo apt update
sudo apt -y install python3 python3-pip python3-paho-mqtt


Validation:

python3 -c "import paho.mqtt.client as mqtt; print('paho-mqtt OK')"


If this fails, do not proceed until fixed.

3) MQTT broker dependency

ASSUMPTION: your Pi runtime expects an MQTT broker reachable at a configured IP/hostname.

If the broker is running on this Pi:

sudo apt -y install mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto


If the broker is elsewhere, skip installation and just validate connectivity:

# Replace with your broker IP/host
BROKER_HOST="192.168.0.10"
mosquitto_sub -h "$BROKER_HOST" -t 'er1/#' -C 1 -W 5

4) Repo location + permissions

ASSUMPTION: repo lives at /opt/escape-room/er.

sudo mkdir -p /opt/escape-room
sudo chown -R "$USER":"$USER" /opt/escape-room
cd /opt/escape-room
# clone or rsync the repo here

5) Install systemd services from repo

From repo root (adjust path if different):

cd /opt/escape-room/er
sudo cp er1/pi-runtime/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload


Enable + start (match actual service filenames in your repo):

sudo systemctl enable --now er1-mqtt-log.service
sudo systemctl enable --now er1-ota-verify.service


Check status:

systemctl status er1-mqtt-log.service --no-pager
systemctl status er1-ota-verify.service --no-pager

6) Logs + health validation

Tail logs:

journalctl -u er1-mqtt-log.service -f
# in another terminal
journalctl -u er1-ota-verify.service -f


Minimum expected behavior:

Services stay active (running).

No repeated crash loops.

OTA verify logs parsed heartbeats/commands when MQTT traffic exists.

7) Post-provision checklist

 python3 -c "import paho.mqtt.client" succeeds

 MQTT connectivity verified (mosquitto_sub works)

 er1-mqtt-log.service active

 er1-ota-verify.service active

 Logs are being written where expected (ASSUMPTION: er1/pi-runtime/logs/)

8) What deploy does NOT do

Deploy must NOT:

run apt install

run pip install

modify OS packages

“fix” dependencies dynamically

If a service needs a package, it belongs in this provisioning document (or a provisioning script).
