import logging
import time
import functools
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import LOGS_DIR


def _get_logger() -> logging.Logger:
    logger = logging.getLogger("email_assistant")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    LOGS_DIR.mkdir(exist_ok=True)

    # File handler with rotation (10MB, keep 5 backups)
    fh = RotatingFileHandler(
        LOGS_DIR / "agent.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(logging.INFO)

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


logger = _get_logger()


def log_agent_run(agent_name: str, msg_id: str, status: str, duration_ms: float, detail: str = ""):
    """Write a structured log entry for an agent run."""
    logger.info(
        f"agent={agent_name} | msg_id={msg_id} | status={status} | "
        f"duration_ms={duration_ms:.1f} | {detail}"
    )


def monitor(agent_name: str):
    """Decorator: wrap any agent function to auto-log execution status and timing."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            # Try to extract msg_id from first positional arg (EmailMessage) or kwargs
            msg_id = "unknown"
            if args:
                first = args[0]
                if hasattr(first, "id"):
                    msg_id = first.id
            elif "msg" in kwargs and hasattr(kwargs["msg"], "id"):
                msg_id = kwargs["msg"].id

            start = time.time()
            try:
                result = fn(*args, **kwargs)
                duration_ms = (time.time() - start) * 1000
                log_agent_run(agent_name, msg_id, "success", duration_ms)
                return result
            except Exception as exc:
                duration_ms = (time.time() - start) * 1000
                log_agent_run(agent_name, msg_id, "error", duration_ms, detail=str(exc))
                raise
        return wrapper
    return decorator
