"""Structured logging and request-correlation coverage.

The contract: a student who reports "my upload failed" has to be able to quote
an id that finds the matching log line, and every line emitted while handling
that request has to carry the same id -- including the access line, which is
written *after* the response body has already been produced.
"""

import json
import logging
import uuid

import pytest

from app.core.logging_config import (
    ConsoleFormatter,
    JsonFormatter,
    RequestContextFilter,
    configure_logging,
    request_id_var,
)
from app.core.middleware import (
    MAX_INBOUND_ID_LENGTH,
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
)


def _record(**extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="app.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello", args=(), exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    RequestContextFilter().filter(record)
    return record


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def test_json_formatter_emits_one_object_per_record():
    payload = json.loads(JsonFormatter().format(_record(method="GET", status_code=200)))
    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["method"] == "GET"
    assert payload["status_code"] == 200
    assert isinstance(payload["timestamp"], float)


def test_json_formatter_keeps_request_id_a_string():
    """Consumers should never have to branch on the key being absent or null."""
    assert json.loads(JsonFormatter().format(_record()))["request_id"] == "-"


def test_json_formatter_survives_an_unserializable_field():
    """A bad extra must not raise inside a logging call and take down the request.

    ``default=str`` already absorbs most objects, so the genuinely hostile case
    is a self-referential structure, which no fallback can stringify.
    """
    circular = []
    circular.append(circular)
    payload = json.loads(JsonFormatter().format(_record(blob=circular)))
    assert payload["unserializable_fields"] is True
    assert payload["message"] == "hello"
    assert payload["request_id"] == "-"


def test_json_formatter_includes_the_exception_text():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="app.test", level=logging.ERROR, pathname=__file__, lineno=1,
            msg="failed", args=(), exc_info=sys.exc_info(),
        )
    RequestContextFilter().filter(record)
    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in payload["exception"]


def test_console_formatter_shows_the_request_id():
    """Without the id in the line, interleaved requests are unfollowable."""
    line = ConsoleFormatter().format(_record())
    assert "[-]" in line
    assert "hello" in line


def test_console_formatter_tolerates_a_record_without_a_request_id():
    bare = logging.LogRecord(
        name="app.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello", args=(), exc_info=None,
    )
    assert "[-]" in ConsoleFormatter().format(bare)


def test_request_context_filter_uses_the_context_var():
    token = request_id_var.set("abc123")
    try:
        assert _record().request_id == "abc123"
    finally:
        request_id_var.reset(token)


def test_request_context_filter_does_not_leak_across_contexts():
    """A finished request must not colour the next one's lines."""
    token = request_id_var.set("first")
    request_id_var.reset(token)
    assert _record().request_id == "-"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_configure_logging_replaces_handlers_and_is_idempotent():
    root = logging.getLogger()
    original = list(root.handlers)
    try:
        configure_logging()
        first = list(root.handlers)
        configure_logging()
        assert len(root.handlers) == len(first) == 1
        assert any(isinstance(f, RequestContextFilter) for f in root.handlers[0].filters)
    finally:
        root.handlers = original


# ---------------------------------------------------------------------------
# Request correlation, end to end through the real app
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_response_carries_a_request_id_header(client, registered_user):
    res = await client.get("/api/v1/subjects", headers=registered_user["headers"])
    assert res.status_code == 200
    assert res.headers.get(REQUEST_ID_HEADER)


@pytest.mark.asyncio
async def test_inbound_request_id_is_reused(client, registered_user):
    supplied = "trace-abc-123"
    res = await client.get(
        "/api/v1/subjects",
        headers={**registered_user["headers"], REQUEST_ID_HEADER: supplied},
    )
    assert res.headers[REQUEST_ID_HEADER] == supplied


@pytest.mark.asyncio
async def test_oversized_inbound_id_is_replaced(client, registered_user):
    """An unbounded client-supplied string would be copied into every log line."""
    hostile = "x" * (MAX_INBOUND_ID_LENGTH + 1)
    res = await client.get(
        "/api/v1/subjects",
        headers={**registered_user["headers"], REQUEST_ID_HEADER: hostile},
    )
    echoed = res.headers[REQUEST_ID_HEADER]
    assert echoed != hostile
    assert len(echoed) <= 32


@pytest.mark.asyncio
async def test_blank_inbound_id_is_replaced(client, registered_user):
    res = await client.get(
        "/api/v1/subjects",
        headers={**registered_user["headers"], REQUEST_ID_HEADER: "   "},
    )
    assert res.headers[REQUEST_ID_HEADER].strip() != ""


@pytest.mark.asyncio
async def test_each_request_gets_a_distinct_id(client, registered_user):
    first = await client.get("/api/v1/subjects", headers=registered_user["headers"])
    second = await client.get("/api/v1/subjects", headers=registered_user["headers"])
    assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]


@pytest.mark.asyncio
async def test_error_responses_also_carry_a_request_id(client, registered_user):
    """A 404 or 500 is exactly when a student needs to quote the id."""
    res = await client.get(
        f"/api/v1/subjects/{uuid.uuid4()}", headers=registered_user["headers"]
    )
    assert res.status_code == 404
    assert res.headers.get(REQUEST_ID_HEADER)


@pytest.mark.asyncio
async def test_request_id_is_available_on_request_state():
    """Routes need it to stamp onto domain rows (documents, attempts).

    Exercised on a private app rather than by bolting a route onto the real one:
    registering a test route on the shared application leaks it into every other
    test in the session.
    """
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from httpx import ASGITransport, AsyncClient

    seen = {}

    async def probe(request: Request) -> JSONResponse:
        seen["state"] = getattr(request.state, "request_id", None)
        seen["context"] = request_id_var.get()
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/probe", probe)])
    app.add_middleware(RequestContextMiddleware)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://probe") as ac:
        res = await ac.get("/probe", headers={REQUEST_ID_HEADER: "probe-1"})

    assert res.status_code == 200
    assert res.headers[REQUEST_ID_HEADER] == "probe-1"
    assert seen["state"] == "probe-1"
    # The ContextVar is live while the handler runs, so any log line it emits
    # is already correlated.
    assert seen["context"] == "probe-1"


@pytest.mark.asyncio
async def test_context_is_cleared_after_the_request_completes(client, registered_user):
    """Leaking an id across requests would silently mis-attribute later logs."""
    await client.get("/api/v1/subjects", headers=registered_user["headers"])
    assert request_id_var.get() in (None, "-")


@pytest.mark.asyncio
async def test_access_log_carries_the_request_id():
    """The regression this middleware exists to prevent.

    The completion line is written after the response body is produced, and the
    context is unwound in a ``finally``. If the reset ran first, every access log
    line would read ``request_id="-"`` and the whole feature would be inert while
    still looking correct in the response header.
    """
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from httpx import ASGITransport, AsyncClient

    async def ok(request) -> JSONResponse:
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/ok", ok)])
    app.add_middleware(RequestContextMiddleware)

    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

        # In production RequestContextFilter is attached to the root handler, so
        # the captured records must be filtered the same way to be comparable.
        def filter(self, record):
            return RequestContextFilter().filter(record)

    logger = logging.getLogger("app.request")
    previous_level, previous_propagate = logger.level, logger.propagate
    capture = Capture()
    logger.addHandler(capture)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://probe"
        ) as ac:
            res = await ac.get("/ok", headers={REQUEST_ID_HEADER: "log-check"})
    finally:
        logger.removeHandler(capture)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate

    completed = [r for r in records if r.getMessage() == "request_completed"]
    assert len(completed) == 1
    record = completed[0]
    assert record.request_id == "log-check"
    assert record.status_code == 200
    assert record.method == "GET"
    assert record.path == "/ok"
    assert record.duration_ms >= 0
    assert res.headers[REQUEST_ID_HEADER] == "log-check"


@pytest.mark.asyncio
async def test_health_checks_are_not_logged(client):
    """Otherwise the liveness probe drowns out real traffic in the access log."""
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

        # In production RequestContextFilter is attached to the root handler, so
        # the captured records must be filtered the same way to be comparable.
        def filter(self, record):
            return RequestContextFilter().filter(record)

    logger = logging.getLogger("app.request")
    previous_level, previous_propagate = logger.level, logger.propagate
    capture = Capture()
    logger.addHandler(capture)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        res = await client.get("/api/v1/health")
    finally:
        logger.removeHandler(capture)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate

    assert res.status_code == 200
    assert res.headers.get(REQUEST_ID_HEADER)  # still correlated, just not logged
    assert not [r for r in records if r.getMessage() == "request_completed"]
