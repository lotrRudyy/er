#!/usr/bin/env python3
"""ota_publish.py

Compute OTA sha256 on the Pi and publish the UPDATE command with a JSON payload.

- Reads firmware from /home/rudyy/er1/node_firmware by default
- Publishes to: <cmd_node>/cmd
    UPDATE {"version":...,"build":...,"target":...,"url":...,"sha256":...,"size":...}
- Default URL matches ota_http.py: http://<http-host>/node_firmware/<firmware>.bin

Optional verification (recommended for er1 ota):
  --verify --timeout 25 --up-max 10

Verification behavior:
- Subscribes to <node>/ota and <node>/hb (and <node>/log for visibility)
- Fails fast if any subscribed node reports st == "OTA_FAIL" on /ota
- Succeeds when every verify node reports hb.build == expected build
  and (if --up-max > 0) hb.up <= up_max (to ensure a fresh reboot)

Dependencies on the Pi:
  pip install paho-mqtt

Exit codes:
  0  success
  1  error (bad args, file missing, mqtt publish fail, etc.)
  2  verify timeout
  3  verify saw OTA_FAIL
  4  verify saw expected build but uptime too large (likely old firmware still running)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

DEFAULT_FIRMWARE_DIR = Path("/home/rudyy/er1/node_firmware")
DEFAULT_HTTP_HOST = "192.168.0.10"
DEFAULT_BROKER = "192.168.0.10"

DEPLOYMENTS: dict[str, dict[str, object]] = {
    "maglock": {
        "env": "maglock",
        "dev": "maglock",
        "cmd_node": "maglock",
        "firmware": "maglock.bin",
        "verify_nodes": ("maglock",),
    },
    "images_piano": {
        "env": "images_piano",
        "dev": "images_piano",
        "cmd_node": "images_piano",
        "firmware": "images_piano.bin",
        "verify_nodes": ("images_piano",),
    },
    "chess": {
        "env": "chess",
        "dev": "chess",
        "cmd_node": "chess",
        "firmware": "chess.bin",
        "verify_nodes": ("chess",),
    },
    "knocking": {
        "env": "knocking",
        "dev": "knocking",
        "cmd_node": "knocking",
        "firmware": "knocking.bin",
        "verify_nodes": ("knocking",),
    },
    "candles": {
        "env": "candles",
        "dev": "candles",
        "cmd_node": "candles",
        "firmware": "candles.bin",
        "verify_nodes": ("candles",),
    },
    "star_sky": {
        "env": "star_sky",
        "dev": "star_sky",
        "cmd_node": "star_sky",
        "firmware": "star_sky.bin",
        "verify_nodes": ("star_sky",),
    },
    "star_slider": {
        "env": "star_slider",
        "dev": "star_slider",
        "cmd_node": "star_slider",
        "firmware": "star_slider.bin",
        "verify_nodes": ("star_slider",),
    },
    "stop_timer": {
        "env": "stop_timer",
        "dev": "stop_timer",
        "cmd_node": "stop_timer",
        "firmware": "stop_timer.bin",
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


def make_mqtt_client():
    import paho.mqtt.client as mqtt  # type: ignore

    # Reduce "Callback API version 1 is deprecated" if possible
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except Exception:
        return mqtt.Client()


def publish(broker: str, port: int, topic: str, payload: str) -> None:
    try:
        import paho.mqtt.client as mqtt  # type: ignore
    except ImportError as exc:
        raise OtaPublishError("paho-mqtt not installed on Pi; pip install paho-mqtt") from exc

    client = make_mqtt_client()
    rc = client.connect(broker, port, 30)
    if rc != 0:
        raise OtaPublishError(f"MQTT connect failed rc={rc} ({mqtt.error_string(rc)})")

    client.loop_start()
    try:
        info = client.publish(topic, payload)
        info.wait_for_publish(timeout=5)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise OtaPublishError(f"MQTT publish failed rc={info.rc} ({mqtt.error_string(info.rc)})")
    finally:
        client.loop_stop()
        client.disconnect()


def normalize_url(url: str, http_host: str) -> str:
    """Accept full URL or a path like /node_firmware/x.bin and normalize to full http:// URL."""
    u = (url or "").strip()
    if not u:
        raise OtaPublishError("URL is empty")

    parsed = urlparse(u)
    if parsed.scheme in ("http", "https"):
        return u

    # treat as path
    path = u
    if not path.startswith("/"):
        path = "/" + path
    return f"http://{http_host}{path}"


def _split_csv(s: Optional[str]) -> Optional[tuple[str, ...]]:
    if s is None:
        return None
    parts = [p.strip() for p in s.split(",")]
    parts = [p for p in parts if p]
    return tuple(parts) if parts else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish an OTA command from the Pi with SHA-256 validation (no PSK)."
    )
    parser.add_argument("--dev", required=True, help="Device name (topic prefix / deployment key)")
    parser.add_argument(
        "--cmd-node",
        dest="cmd_node",
        help="MQTT command node for UPDATE topic (defaults from deployment map or dev)",
    )
    parser.add_argument("--broker", default=DEFAULT_BROKER, help="MQTT broker host")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")

    parser.add_argument(
        "--http-host",
        dest="http_host",
        default=DEFAULT_HTTP_HOST,
        help="HTTP host serving firmware (default: 192.168.0.10)",
    )
    parser.add_argument(
        "--url",
        help=(
            "OTA URL (full http(s)://... or path like /node_firmware/x.bin). "
            "Defaults to /node_firmware/<firmware>."
        ),
    )

    parser.add_argument(
        "--firmware-name",
        dest="firmware_name",
        help="Firmware filename (defaults from deployment map or <dev>.bin)",
    )
    # Back-compat alias (underscore)
    parser.add_argument(
        "--firmware_name",
        dest="firmware_name_legacy",
        help=argparse.SUPPRESS,
    )

    parser.add_argument(
        "--file",
        dest="firmware_path",
        help="Firmware file path on the Pi (defaults to /home/rudyy/er1/node_firmware/<firmware_name>)",
    )
    parser.add_argument("--version", required=True, help="Firmware version string to announce")
    parser.add_argument("--build", required=True, help="Firmware build string to announce")
    parser.add_argument(
        "--target",
        help="Expected target node id (defaults from deployment map or dev)",
    )

    parser.add_argument("--dry-run", action="store_true", help="Print payload without publishing")

    # Verification (runs on the Pi)
    parser.add_argument("--verify", action="store_true", help="Verify OTA completes by watching MQTT")
    parser.add_argument(
        "--verify-nodes",
        dest="verify_nodes",
        help="Comma-separated nodes to verify (defaults from deployment map or dev)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=25,
        help="Verify timeout seconds (default: 25)",
    )
    parser.add_argument(
        "--up-max",
        dest="up_max",
        type=int,
        default=10,
        help="Max allowed hb.up at success time (0 disables check). Default: 10",
    )

    return parser.parse_args()


@dataclass
class VerifyState:
    expected_build: str
    up_max: int
    ok_nodes: set[str] = field(default_factory=set)
    fail_reason: Optional[str] = None
    fail_code: int = 0


def verify_ota(broker: str, port: int, nodes: tuple[str, ...], expected_build: str, timeout_s: int, up_max: int) -> int:
    """Return exit code (0 ok, 2 timeout, 3 ota_fail, 4 uptime too large)."""
    try:
        import paho.mqtt.client as mqtt  # type: ignore
    except ImportError:
        print("ota_publish: paho-mqtt not installed on Pi; pip install paho-mqtt", file=sys.stderr)
        return 1

    st = VerifyState(expected_build=expected_build, up_max=up_max)

    topics = []
    for n in nodes:
        topics.extend([(f"{n}/hb", 0), (f"{n}/ota", 0), (f"{n}/log", 0)])

    def on_message(client, userdata, msg):
        # msg.topic is str, payload bytes
        topic = msg.topic
        try:
            payload_txt = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload_txt = ""

        # Always print for visibility (matches how mosquitto_sub -v looks)
        print(f"{topic} {payload_txt}")

        # Parse node
        node = topic.split("/", 1)[0] if "/" in topic else topic

        if topic.endswith("/ota"):
            try:
                o = json.loads(payload_txt)
            except Exception:
                return
            if o.get("st") == "OTA_FAIL":
                st.fail_reason = f"{node} reported OTA_FAIL"
                st.fail_code = 3

        if topic.endswith("/hb"):
            try:
                hb = json.loads(payload_txt)
            except Exception:
                return
            b = hb.get("build")
            up = hb.get("up")
            if b == st.expected_build:
                if st.up_max and isinstance(up, int) and up > st.up_max:
                    st.fail_reason = f"{node} build matched but uptime too large (up={up} > {st.up_max})"
                    st.fail_code = 4
                    return
                st.ok_nodes.add(node)

    client = make_mqtt_client()
    client.on_message = on_message

    rc = client.connect(broker, port, 30)
    if rc != 0:
        print(f"ota_publish: MQTT connect failed rc={rc}", file=sys.stderr)
        return 1

    for t, qos in topics:
        client.subscribe(t, qos=qos)

    client.loop_start()
    deadline = time.time() + max(1, timeout_s)
    try:
        print(
            f"== OTA VERIFY: expecting build={expected_build}; nodes={','.join(nodes)}; timeout={timeout_s}s; up_max={up_max} =="
        )

        while time.time() < deadline:
            if st.fail_code:
                print(f"== OTA VERIFY: FAIL ({st.fail_reason}) ==")
                return st.fail_code

            if set(nodes).issubset(st.ok_nodes):
                print("== OTA VERIFY: OK (hb.build matched) ==")
                return 0

            time.sleep(0.05)

        print(f"== OTA VERIFY: FAIL (timeout; build never became {expected_build}) within {timeout_s}s ==")
        return 2
    finally:
        client.loop_stop()
        client.disconnect()


def main() -> int:
    args = parse_args()
    deployment = DEPLOYMENTS.get(args.dev, {})

    cmd_node = args.cmd_node or deployment.get("cmd_node") or args.dev

    firmware_name = (
        args.firmware_name
        or getattr(args, "firmware_name_legacy", None)
        or deployment.get("firmware")
        or f"{args.dev}.bin"
    )

    firmware_path = Path(args.firmware_path) if args.firmware_path else (DEFAULT_FIRMWARE_DIR / str(firmware_name))

    target = args.target or deployment.get("target") or args.dev
    if not target:
        raise OtaPublishError("Target must be provided (via --target or deployment map)")

    version = args.version.strip()
    if not version:
        raise OtaPublishError("Version must be provided")

    build = args.build.strip()
    if not build:
        raise OtaPublishError("Build must be provided")

    default_path = f"/node_firmware/{firmware_name}"
    url = normalize_url(args.url or default_path, args.http_host)

    # Determine verify nodes
    vn_cli = _split_csv(args.verify_nodes)
    if vn_cli is not None:
        verify_nodes = vn_cli
    else:
        verify_nodes = tuple(deployment.get("verify_nodes", (args.dev,)))  # type: ignore
        if not verify_nodes:
            verify_nodes = (args.dev,)

    try:
        sha_hex = sha256_file(firmware_path)
        try:
            size_bytes = firmware_path.stat().st_size
        except OSError as exc:
            raise OtaPublishError(f"Failed to stat firmware {firmware_path}: {exc}") from exc
        if size_bytes <= 0:
            raise OtaPublishError(f"Firmware size invalid: {size_bytes}")

        topic = f"{cmd_node}/cmd"

        # IMPORTANT:
        # We intentionally DO NOT include an OTA id ("id") because legacy nodes truncate
        # the JSON payload; id is not required for OTA correctness.
        payload_obj = {
            "version": version,
            "build": build,
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
        print(f"Build    : {build}")
        print(f"Target   : {target}")
        print(f"CmdNode  : {cmd_node}")
        print(f"CmdTopic : {topic}")
        print(f"Payload  : {payload}")

        if args.dry_run:
            print("Dry run: not publishing")
            return 0

        publish(args.broker, args.port, topic, payload)
        print("Published OTA command")

        if args.verify:
            return verify_ota(
                broker=args.broker,
                port=args.port,
                nodes=verify_nodes,
                expected_build=build,
                timeout_s=int(args.timeout),
                up_max=int(args.up_max),
            )

        return 0

    except OtaPublishError as exc:
        print(f"ota_publish: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
