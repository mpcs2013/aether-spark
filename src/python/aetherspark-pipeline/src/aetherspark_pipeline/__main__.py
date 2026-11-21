"""`python -m aetherspark_pipeline`: the worker, configured from the environment (settings.py)."""

from __future__ import annotations

import logging
import os
import signal
import sys
from types import FrameType

from . import jobs
from .clickhouse import ClickHouse
from .settings import Settings
from .worker import Stopping, run


def _stop(signum: int, frame: FrameType | None) -> None:
    raise Stopping


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stdout
    )
    settings = Settings.from_env(os.environ)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log = logging.getLogger("pipeline")
    store = ClickHouse(
        settings.clickhouse_host,
        settings.clickhouse_port,
        settings.clickhouse_user,
        settings.clickhouse_password_file,
        settings.root_certificate,
    )
    try:
        with jobs.connect(settings) as conn:
            log.info("pipeline worker started: checks, Arelle offline, facts into ClickHouse")
            run(settings, conn, store)
    except Stopping:
        log.info("pipeline worker stopped")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
