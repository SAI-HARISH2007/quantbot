"""Structured logging: rich console + rotating JSON-ish file logs."""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from rich.logging import RichHandler


def setup_logging(level: str = "INFO", log_dir: Path = Path("logs")) -> None:
    log_dir.mkdir(exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()

    console = RichHandler(rich_tracebacks=True, show_path=False)
    console.setFormatter(logging.Formatter("%(message)s", datefmt="%H:%M:%S"))
    root.addHandler(console)

    file_h = logging.handlers.RotatingFileHandler(
        log_dir / "quantbot.log", maxBytes=10_000_000, backupCount=5
    )
    file_h.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(file_h)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
