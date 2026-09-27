"""Application logging setup.

Two constraints shape this module:

1. **Never configure the root logger as an import side effect.** ``main.py``
   calls :func:`configure_logging` inside the lifespan, and the test suite
   imports the same modules without ever starting the app. A library that
   hijacks logging at import time is the single most common way to make an
   otherwise well-behaved test suite unobservable.

2. **Every log record must be attributable.** A background document-ingest task
   finishes long after the HTTP response, so a request id has to survive on the
   record itself. :class:`RequestContextFilter` does that by reading a
   :class:`contextvars.ContextVar`, which is correct for both ``await``-based
   request handling and detached background tasks.
"""

import json
import logging
import sys
from contextvars import ContextVar
from typing import Any, Dict, Optional

from app.core.config import settings

# Holds the current request id. ``ContextVar`` rather than a thread-local so that
# the value follows an async task across every await point.
request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)

# Attributes LogRecord always carries. Anything else a caller passes via
# ``extra=`` is a structured field worth emitting.
_RESERVED = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "module", "msecs",
        "message", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "taskName",
    }
)


class RequestContextFilter(logging.Filter):
    """Copies the active request id onto every record it sees.

    A missing id becomes ``"-"`` rather than ``None`` so JSON consumers always
    get a string and never have to branch on the key being present.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"  # type: ignore[attr-defined]
        return True


def _json_fields(record: logging.LogRecord) -> Dict[str, Any]:
    fields: Dict[str, Any] = {
        "timestamp": record.created,
        "level": record.levelname,
        "logger": record.name,
        "message": record.getMessage(),
        "request_id": getattr(record, "request_id", "-"),
    }
    for key, value in record.__dict__.items():
        if key in _RESERVED or key == "request_id":
            continue
        if key.startswith("_"):
            continue
        fields[key] = value
    if record.exc_info:
        fields["exception"] = _format_exception(record)
    return fields


def _format_exception(record: logging.LogRecord) -> str:
    return logging.Formatter().formatException(record.exc_info)


class JsonFormatter(logging.Formatter):
    """Emits one JSON object per record, for log shippers and ``jq``."""

    def format(self, record: logging.LogRecord) -> str:
        fields = _json_fields(record)
        try:
            return json.dumps(fields, default=str)
        except (TypeError, ValueError):
            # A non-serializable extra must not take down the request that
            # logged it. Fall back to the human-readable rendering.
            return json.dumps(
                {
                    "timestamp": record.created,
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                    "request_id": getattr(record, "request_id", "-"),
                    "unserializable_fields": True,
                },
                default=str,
            )


class ConsoleFormatter(logging.Formatter):
    """Human-readable single line, with the request id kept visible.

    Without the id in the line prefix, interleaved request logs are impossible
    to follow by eye -- which is exactly the failure mode structured logging is
    supposed to prevent.
    """

    default_fmt = "%(asctime)s %(levelname)-5.5s [%(request_id)s] %(name)s: %(message)s"

    def format(self, record: logging.LogRecord) -> str:
        # `defaults` keeps a record that somehow bypassed RequestContextFilter
        # (a third-party handler, a direct `Formatter` call in a test) from
        # raising KeyError inside the logging call.
        return logging.Formatter(
            self.default_fmt, datefmt="%H:%M:%S", defaults={"request_id": "-"}
        ).format(record)


def configure_logging() -> None:
    """Installs the application handler on the root logger. Idempotent.

    ``force=True`` replaces handlers installed by Alembic or uvicorn so a
    duplicated line never appears after a reload.
    """
    level = settings.log_level_number
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RequestContextFilter())
    handler.setFormatter(JsonFormatter() if settings.LOG_JSON else ConsoleFormatter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]

    # Uvicorn installs its own colourised handlers that would otherwise bypass
    # the request-id filter entirely.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Returns a module logger. Thin wrapper so call sites never import ``logging``."""
    return logging.getLogger(name)
