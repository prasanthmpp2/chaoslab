from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime

_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("log_ctx", default={})


def bind(**kwargs: str | None) -> None:
    cur = dict(_ctx.get())
    cur.update({k: v for k, v in kwargs.items() if v is not None})
    _ctx.set(cur)


def clear() -> None:
    _ctx.set({})


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = _ctx.get()
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "run_id": ctx.get("run_id"),
            "experiment_id": ctx.get("experiment_id"),
            "engine": ctx.get("engine"),
            "worker_id": ctx.get("worker_id"),
            "event": getattr(record, "event", record.getMessage()),
            "error_category": getattr(record, "error_category", None),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
