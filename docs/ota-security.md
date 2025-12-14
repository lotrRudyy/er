# OTA Security (SHA-256 only)

The OTA PSK/HMAC path has been removed. OTA integrity now relies on SHA-256 hashes delivered over the trusted LAN.

## Current controls
- OTA commands are JSON payloads sent as `UPDATE {...}` on `<CmdNode>/cmd` and must include: `id`, `version`, `target`, `url`, `sha256` (and optional `size`).
- Nodes enforce host/path allowlists (`http://192.168.0.10/firmware/...` only), reject HTTPS, stream SHA-256 while flashing, and abort on any mismatch or target mismatch.
- Each OTA persists `id` + `version` and only reports `OTA_OK` after reboot when the running FW_VERSION matches the announced version. Failures are retained on `<node>/ota`.

## Operator expectations
- Keep the Pi + HTTP server on the trusted LAN; do not expose `/firmware` over the internet.
- Use `firmware/ota.ps1` (or `er1 ota <target>`) so the Pi computes the hash/size locally and publishes the correct JSON payload.
- If OTA commands fail, check `<node>/ota` for `OTA_FAIL` details and the Pi logs for `ota_publish.py`.

## Security note
Removing the PSK reduces authentication; ensure network isolation and broker access controls are in place. If stronger auth is needed later, layer it on the MQTT side rather than reintroducing per-node PSKs.
