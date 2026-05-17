"""Structured logging setup."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any


class JsonLineFormatter(logging.Formatter):
    """Emit one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if hasattr(record, "extra_data") and isinstance(record.extra_data, dict):  # type: ignore[attr-defined]
            payload.update(record.extra_data)  # type: ignore[attr-defined]
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO", audit_path: Path | None = None) -> logging.Logger:
    """Set up root logger with stderr + optional JSONL audit file."""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())

    stream = logging.StreamHandler(stream=sys.stderr)
    stream.setFormatter(
        logging.Formatter(
            "%(asctime)sZ %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root.addHandler(stream)

    if audit_path is not None:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        f = logging.FileHandler(audit_path, encoding="utf-8")
        f.setFormatter(JsonLineFormatter())
        f.setLevel(logging.INFO)
        root.addHandler(f)

    return root


def with_extra(logger: logging.Logger, level: int, msg: str, **fields: Any) -> None:
    logger.log(level, msg, extra={"extra_data": fields})
