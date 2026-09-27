"""Request-scoped correlation ids and access logging.

The API is not currently served behind a tracing proxy, so nothing in the
infrastructure attaches an id to an inbound request. Without one, a student
who reports "my upload failed" gives us no way to find the matching log line,
and a background ingest task that fails minutes after its 201 response has no
link back to the request that created it.
"""

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging_config import get_logger, request_id_var

logger = get_logger("app.request")

REQUEST_ID_HEADER = "X-Request-ID"
# Bound on the client-supplied value so a hostile or buggy caller cannot inject
# an unbounded string into every log line.
MAX_INBOUND_ID_LENGTH = 64


def _resolve_request_id(request: Request) -> str:
    """Reuses a well-formed inbound id, otherwise mints a fresh one."""
    supplied = request.headers.get(REQUEST_ID_HEADER, "")
    candidate = supplied.strip()
    if candidate and len(candidate) <= MAX_INBOUND_ID_LENGTH:
        return candidate
    return uuid.uuid4().hex


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, makes it observable, and logs one line per request.

    The id is stored three ways on purpose:

    * a :class:`~contextvars.ContextVar`, so every log line emitted while
      handling this request carries it, without each call site having to thread
      an id through its signature;
    * ``request.state.request_id``, so a route can stamp it onto domain rows;
    * an ``X-Request-ID`` response header, so a client can quote it in a bug
      report without needing server access.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = _resolve_request_id(request)
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                # An unhandled exception has no response object to decorate, so
                # the failure is logged here. `call_next` re-raises, so this does
                # not change the response the client ultimately receives.
                logger.exception(
                    "unhandled_request_error",
                    extra={
                        "method": request.method,
                        "path": request.url.path,
                        "duration_ms": _elapsed_ms(started),
                    },
                )
                raise

            # Still inside the token's scope. The reset below is what makes the
            # completion line read `request_id="-"` instead of the real id, so
            # the access log is deliberately emitted before the context is
            # unwound -- the whole point of the middleware is that this line
            # correlates.
            response.headers[REQUEST_ID_HEADER] = request_id
            # Health checks would otherwise dominate the access log.
            if not request.url.path.endswith(("/health", "/health/db")):
                logger.info(
                    "request_completed",
                    extra={
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": response.status_code,
                        "duration_ms": _elapsed_ms(started),
                    },
                )
            return response
        finally:
            request_id_var.reset(token)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
