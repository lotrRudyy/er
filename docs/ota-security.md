# OTA Security (PSK Handling)

Where the OTA PSK lives and how to rotate/recover it without ever storing it on PCs.

## Canonical storage (Pi only)
- Location: `/etc/er1/ota_psk` (single line, raw secret). Directory `/etc/er1` may be `0755`.
- Ownership/permissions: `root:er1` with mode `0640` (group read only). No copy on operator PCs; keep an optional offline USB backup if needed.
- Used by: `~/er1/scripts/ota_publish.py` (invoked by `firmware/ota.ps1` over SSH) to compute `sha256` + `hmac` on the Pi and publish `UPDATE` commands.
- Dependencies: `python3` + `paho-mqtt` installed on the Pi (already required by `ota_verify.py`).

## Setup / reset commands (run on Pi)
```bash
sudo mkdir -p /etc/er1
sudo groupadd -f er1
sudo sh -c 'umask 077; printf "%s\n" "REPLACE_WITH_RANDOM_SECRET" > /etc/er1/ota_psk'
sudo chown root:er1 /etc/er1/ota_psk
sudo chmod 640 /etc/er1/ota_psk
sudo usermod -aG er1 rudyy
sudo ls -la /etc/er1/ota_psk
```
Result should show `-rw-r----- root er1 /etc/er1/ota_psk`.
If you must briefly lock it to root-only before granting group access, use `chmod 600` and `chown root:root /etc/er1/ota_psk` immediately after writing, then switch to `root:er1`/`640` for runtime use.

## Rotation procedure
1. Generate a new random secret and rewrite `/etc/er1/ota_psk` using the commands above (umask keeps it private during write).
2. Re-apply `chown root:er1` and `chmod 640` to enforce permissions.
3. Firmware embeds `OTA_PSK` at compile-time today, so rebuild every env with the new PSK in the build environment and OTA/flash all nodes.
4. Optional sanity check: `python3 /home/rudyy/er1/scripts/ota_publish.py --dev <dev> --url /firmware/<dev>.bin --dry-run` to verify HMAC/sha generation without publishing.

## Recovery procedure
- If `/etc/er1/ota_psk` is lost: restore the secret from the password manager/offline backup and re-apply the setup commands.
- If no backup exists: flash a USB/serial "recovery firmware" built with a known temporary PSK, then immediately rotate again to a new secret and redeploy OTA.
- Never disable OTA validation or ship firmware without `OTA_PSK`; validation must stay enabled.
