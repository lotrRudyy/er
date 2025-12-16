#!/usr/bin/env python3
"""
Minimal HTTP server for OTA firmware artifacts.

Serves /firmware/<name>.bin from /home/rudyy/er1/node_firmware.
"""

from __future__ import annotations

import functools
import http.server
from pathlib import Path

DOC_ROOT = Path("/home/rudyy/er1/node_firmware")
URL_PREFIX = "/firmware"


class FirmwareRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Serve firmware files while accepting the /firmware URL prefix."""

    def translate_path(self, path: str) -> str:
        normalized = self._strip_prefix(path or "/")
        return super().translate_path(normalized)

    def _strip_prefix(self, path: str) -> str:
        if path.startswith(URL_PREFIX):
            trimmed = path[len(URL_PREFIX) :]
            if not trimmed.startswith("/"):
                trimmed = "/" + trimmed
            return trimmed or "/"
        return path


def main() -> None:
    DOC_ROOT.mkdir(parents=True, exist_ok=True)
    handler = functools.partial(FirmwareRequestHandler, directory=str(DOC_ROOT))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", 80), handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
