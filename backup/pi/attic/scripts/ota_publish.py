#!/usr/bin/env python3
"""
Compute OTA sha256 + HMAC on the Pi and publish the UPDATE command.

Reads PSK from /etc/er1/ota_psk (root-owned, group-readable) and publishes
to <dev>/cmd on the configured broker.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import sys
from pathlib import Path

DEFAULT_PSK_PATH = Path("/etc/er1/ota_psk")
DEFAULT_FIRMWARE_DIR = Path("/home/rudyy/firmware")


class OtaPublishError(Exception):
    pass


def read_psk(psk_path: Path) -> str:
    try:
        data = psk_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        raise OtaPublishError(f"PSK file not found at {psk_path}; see docs/ota-security.md")
    except PermissionError as exc:
        raise OtaPublishError(f"Permission denied reading PSK at {psk_path}: {exc}") from exc
    except OSError as exc:
        raise OtaPublishError(f"Failed to read PSK from {psk_path}: {exc}") from exc

    if not data:
        raise OtaPublishError(f"PSK file {psk_path} is empty")
    return data


def sha256_file(path: Path) -> str:
    if not path.exists():
        raise OtaPublishError(f"Firmware file not found: {path}")
    if not path.is_file():
        raise OtaPublishError(f"Firmware path is not a file: {path}")

    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(8192), b""):
                digest.update(chunk)
    except OSError as exc:
        raise OtaPublishError(f"Failed to read firmware {path}: {exc}") from exc

    return digest.hexdigest()


def compute_hmac(psk: str, sha_hex: str) -> str:
    return hmac.new(psk.encode("utf-8"), sha_hex.encode("utf-8"), hashlib.sha256).hexdigest()


def publish(broker: str, port: int, topic: str, payload: str) -> None:
    try:
        import paho.mqtt.client as mqtt  # type: ignore
    except ImportError as exc:
        raise OtaPublishError("paho-mqtt not installed on Pi; pip install paho-mqtt") from exc

    client = mqtt.Client()
    rc = client.connect(broker, port, 30)
    if rc != 0:
        raise OtaPublishError(f"MQTT connect failed rc={rc} ({mqtt.error_string(rc)})")

    client.loop_start()
    info = client.publish(topic, payload)
    info.wait_for_publish(timeout=5)
    client.loop_stop()
    client.disconnect()

    if info.rc != mqtt.MQTT_ERR_SUCCESS:
        raise OtaPublishError(f"MQTT publish failed rc={info.rc} ({mqtt.error_string(info.rc)})")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish an OTA command from the Pi with HMAC computed locally."
    )
    parser.add_argument("--dev", required=True, help="Device name (topic prefix)")
    parser.add_argument("--broker", default="192.168.0.10", help="MQTT broker host")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--url", help="OTA URL (path or full http:// host/path)")
    parser.add_argument(
        "--file",
        dest="firmware_path",
        help="Firmware file path on the Pi (defaults to /home/rudyy/firmware/<dev>.bin)",
    )
    parser.add_argument("--psk-path", default=str(DEFAULT_PSK_PATH), help="Path to OTA PSK file")
    parser.add_argument("--dry-run", action="store_true", help="Print payload without publishing")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    firmware_path = (
        Path(args.firmware_path) if args.firmware_path else DEFAULT_FIRMWARE_DIR / f"{args.dev}.bin"
    )
    url = args.url or f"/firmware/{firmware_path.name}"
    psk_path = Path(args.psk_path)

    try:
        psk = read_psk(psk_path)
        sha_hex = sha256_file(firmware_path)
        hmac_hex = compute_hmac(psk, sha_hex)
        cmd_node = "images" if args.dev == "images_piano" else args.dev
        topic = f"{cmd_node}/cmd"
        payload = f"UPDATE sha256={sha_hex} hmac={hmac_hex} url={url}"

        print(f"PSK path : {psk_path}")
        print(f"Firmware : {firmware_path}")
        print(f"SHA256   : {sha_hex}")
        print(f"HMAC     : {hmac_hex}")
        print(f"Dev      : {args.dev}")
        print(f"CmdTopic : {topic}")
        print(f"Payload  : {payload}")

        if args.dry_run:
            print("Dry run: not publishing")
            return 0

        publish(args.broker, args.port, topic, payload)
        print("Published OTA command")
        return 0
    except OtaPublishError as exc:
        print(f"ota_publish: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
