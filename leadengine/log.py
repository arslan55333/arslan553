"""Structured logging: readable console output plus a JSON-lines file."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

LOGGER_NAME = "leadengine"


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Pass structured fields with ``extra={"data": {...}}``."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        data = getattr(record, "data", None)
        if isinstance(data, dict):
            entry.update(data)
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Message followed by its structured fields as ``key=value``."""

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        data = getattr(record, "data", None)
        if isinstance(data, dict) and data:
            msg += "  " + " ".join(f"{k}={v}" for k, v in data.items())
        return msg


def setup_logging(level: str = "INFO", log_dir: Path | None = None, console: bool = True) -> logging.Logger:
    """Configure the ``leadengine`` logger. Safe to call more than once."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    if console:
        handler = RichHandler(console=Console(stderr=True), show_path=False, markup=False, rich_tracebacks=False)
        handler.setLevel(level)
        handler.setFormatter(ConsoleFormatter())
        logger.addHandler(handler)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "leadengine.jsonl", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(JsonFormatter())
        logger.addHandler(file_handler)

    # httpx logs full request URLs at INFO, which can include API keys.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
