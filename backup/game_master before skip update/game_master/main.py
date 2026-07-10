from __future__ import annotations

import logging
import signal
import time

from game_master import GameMaster


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main() -> int:
    configure_logging()
    gm = GameMaster()
    gm.start()

    def _stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        gm.stop()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
