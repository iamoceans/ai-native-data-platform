"""Structured JSON logging (spec section 29).

Log fields: timestamp, level, service, request_id, trace_id, user_id,
analysis_id, query_id, provider, duration_ms, outcome, error_code.
Credentials, connection strings, full raw rows and raw prompts are never
logged.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

_RESERVED = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
    "message",
}

_ALLOWED_EXTRA = {
    "service",
    "request_id",
    "trace_id",
    "user_id",
    "analysis_id",
    "query_id",
    "datasource_id",
    "dataset_id",
    "provider",
    "worker_id",
    "duration_ms",
    "outcome",
    "error_code",
    "event",
    "step",
}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _ALLOWED_EXTRA and value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(service: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    # uvicorn access logs duplicate structured output; keep warnings and above.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
