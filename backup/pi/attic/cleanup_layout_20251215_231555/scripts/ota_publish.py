#!/usr/bin/env python3
"""
Compute OTA sha256 on the Pi and publish the UPDATE command with a JSON payload.
"""
from __future__ import annotations

import argparse
import json
import hashlib
import sys
import uuid
from pathlib import Path

DEFAULT_FIRMWARE_DIR = Path("/home/rudyy/firmware")

DEPLOYMENTS: dict[str, dict[str, object]] = {
    "maglock": {
        "env": "maglock",
        "dev": "maglock",
        "cmd_node": "maglock",
        "firmware": "maglock.bin",
        "legacy": ("maglock_ctrl.bin",),
        "verify_nodes": ("maglock",),
    },
    "images_piano": {
        "env": "images_piano",
        "dev": "images_piano",
        "cmd_node": "images",
        "firmware": "images_piano.bin",
        "legacy": (),
        "verify_nodes": ("images", "piano"),
    },
    "chess": {
        "env": "chess",
        "dev": "chess",
        "cmd_node": "chess",
        "firmware": "chess.bin",
        "legacy": (),
        "verify_nodes": ("chess",),
    },
    "knocking": {
        "env": "knocking",
        "dev": "knocking",
        "cmd_node": "knocking",
        "firmware": "knocking.bin",
        "legacy": (),
        "verify_nodes": ("knocking",),
    },
    "candles": {
        "env": "candles",
        "dev": "candles",
        "cmd_node": "candles",
        "firmware": "candles.bin",
        "legacy": (),
        "verify_nodes": ("candles",),
    },
    "star_sky": {
        "env": "star_sky",
        "dev": "star_sky",
        "cmd_node": "star_sky",
        "firmware": "star_sky.bin",
        "legacy": (),
        "verify_nodes": ("star_sky",),
    },
    "star_slider": {
        "env": "star_slider",
        "dev": "star_slider",
        "cmd_node": "star_slider",
        "firmware": "star_slider.bin",
        "legacy": (),
        "verify_nodes": ("star_slider",),
    },
    "stop_timer": {
        "env": "stop_timer",
        "dev": "stop_timer",
        "cmd_node": "stop_timer",
        "firmware": "stop_timer.bin",
        "legacy": (),
        "verify_nodes": ("stop_timer",),
    },
}


class OtaPublishError(Exception):
    pass


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
        description="Publish an OTA command from the Pi with SHA-256 validation (no PSK)."
    )
    parser.add_argument("--dev", required=True, help="Device name (topic prefix)")
    parser.add_argument(
        "--cmd-node",
        dest="cmd_node",
        help="MQTT command node for UPDATE topic (defaults from deployment map or dev)",
    )
    parser.add_argument("--broker", default="192.168.0.10", help="MQTT broker host")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--url", help="OTA URL (path or full http:// host/path)")
    parser.add_argument(
        "--firmware-name",
        dest="firmware_name",
        help="Firmware filename (defaults from deployment map or <dev>.bin)",
    )
    parser.add_argument(
        "--file",
        dest="firmware_path",
        help="Firmware file path on the Pi (defaults to /home/rudyy/firmware/<firmware_name>)",
    )
    parser.add_argument("--version", required=True, help="Firmware version string to announce")
    parser.add_argument("--target", help="Expected target node id (defaults from deployment map or dev)")
    parser.add_argument("--id", dest="ota_id", help="OTA update id (nonce); defaults to random UUID")
    parser.add_argument("--dry-run", action="store_true", help="Print payload without publishing")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    deployment = DEPLOYMENTS.get(args.dev, {})
    cmd_node = args.cmd_node or deployment.get("cmd_node") or args.dev
    firmware_name = args.firmware_name or deployment.get("firmware") or f"{args.dev}.bin"
    firmware_path = (
        Path(args.firmware_path) if args.firmware_path else DEFAULT_FIRMWARE_DIR / firmware_name
    )
    target = args.target or deployment.get("target") or args.dev
    if not target:
        raise OtaPublishError("Target must be provided (via --target or deployment map)")
    version = args.version.strip()
    if not version:
        raise OtaPublishError("Version must be provided")
    url = args.url or f"http://192.168.0.10/firmware/{firmware_name}"
    ota_id = args.ota_id or uuid.uuid4().hex

    try:
        sha_hex = sha256_file(firmware_path)
        try:
            size_bytes = firmware_path.stat().st_size
        except OSError as exc:
            raise OtaPublishError(f"Failed to stat firmware {firmware_path}: {exc}") from exc
        if size_bytes <= 0:
            raise OtaPublishError(f"Firmware size invalid: {size_bytes}")
        topic = f"{cmd_node}/cmd"
        payload_obj = {
            "id": ota_id,
            "version": version,
            "target": target,
            "url": url,
            "sha256": sha_hex,
            "size": size_bytes,
        }
        payload_json = json.dumps(payload_obj, separators=(",", ":"))
        payload = f"UPDATE {payload_json}"

        print(f"Firmware : {firmware_path}")
        print(f"Size     : {size_bytes}")
        print(f"SHA256   : {sha_hex}")
        print(f"URL      : {url}")
        print(f"Dev      : {args.dev}")
        print(f"Version  : {version}")
        print(f"Target   : {target}")
        print(f"OtaId    : {ota_id}")
        print(f"CmdNode  : {cmd_node}")
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
