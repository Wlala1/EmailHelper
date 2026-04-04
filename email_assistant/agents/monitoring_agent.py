from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from config import LOGS_DIR


def _get_logger() -> logging.Logger:
    logger = logging.getLogger("email_assistant")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    LOGS_DIR.mkdir(exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        LOGS_DIR / "agent.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


logger = _get_logger()
