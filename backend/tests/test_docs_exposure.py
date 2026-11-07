"""B3: whether a deployment publishes its own API surface.

The requirement has two halves that pull in opposite directions -- developers
need /docs, and a public host should not hand out a map of every route and
schema -- so both are pinned down here, together with the property that makes the
second one real: turning the UI off is not enough if the schema is still served.
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.main import (
    DOCS_AVAILABLE,
    DOCS_URL,
    OPENAPI_URL,
    REDOC_URL,
    resolve_docs_urls,
)


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _app_for(is_production: bool) -> FastAPI:
    """Builds an app the way app/main.py does, from the resolved URLs."""
    urls = resolve_docs_urls(is_production)
    return FastAPI(
        title="Test",
        docs_url=urls.docs,
        redoc_url=urls.redoc,
        openapi_url=urls.openapi,
    )


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


def test_production_disables_every_documentation_url():
    urls = resolve_docs_urls(is_production=True)

    assert urls.docs is None
    assert urls.redoc is None
    # Not just the UI. /openapi.json is the machine-readable copy of the same
    # document and is the more convenient thing to scrape.
    assert urls.openapi is None
    assert urls.enabled is False


def test_development_keeps_every_documentation_url():
    urls = resolve_docs_urls(is_production=False)

    assert urls.docs == "/api/v1/docs"
    assert urls.redoc == "/api/v1/redoc"
    assert urls.openapi == "/api/v1/openapi.json"
    assert urls.enabled is True


def test_the_test_environment_keeps_the_docs():
    """Guards against a future edit flipping the default the wrong way."""
    assert DOCS_AVAILABLE is True
    assert DOCS_URL and REDOC_URL and OPENAPI_URL


# ---------------------------------------------------------------------------
# What the URLs actually do
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_development_app_serves_its_docs():
    async with _client(_app_for(is_production=False)) as ac:
        assert (await ac.get("/api/v1/docs")).status_code == 200
        assert (await ac.get("/api/v1/redoc")).status_code == 200
        assert (await ac.get("/api/v1/openapi.json")).status_code == 200


@pytest.mark.asyncio
async def test_a_production_app_serves_none_of_them():
    async with _client(_app_for(is_production=True)) as ac:
        for path in ("/api/v1/docs", "/api/v1/redoc", "/api/v1/openapi.json"):
            res = await ac.get(path)
            assert res.status_code == 404, path


# ---------------------------------------------------------------------------
# Against the real application
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_running_app_still_serves_its_schema(client):
    """Development behaviour is unchanged, so the suite can inspect routes."""
    assert (await client.get("/api/v1/docs")).status_code == 200
    assert (await client.get("/api/v1/openapi.json")).status_code == 200


@pytest.mark.asyncio
async def test_the_root_endpoint_does_not_advertise_a_missing_docs_url(client):
    """A 404'd docs URL is worse than silence, so / tracks the decision."""
    body = (await client.get("/")).json()

    assert body["docs_url"] == DOCS_URL
    assert body["health_check"] == "/api/v1/health"
