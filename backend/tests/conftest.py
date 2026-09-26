"""Shared pytest fixtures for the backend suite.

Two rules this module enforces for every test:

1. **Offline by default.** ``GEMINI_API_KEY`` is neutralised *before* any
   ``app.*`` module is imported, and the Gemini singletons are forced into
   their deterministic fallback mode. The suite therefore behaves identically
   whether or not a real key is present in the environment or in ``.env``.
2. **Never touch a developer's database.** ``DATABASE_URL`` is redirected to an
   in-memory SQLite database. The default suite runs on its own SQLite engine;
   PostgreSQL/pgvector tests opt in explicitly via the ``postgres`` marker.
"""

import os

# --- Offline / isolation hardening. Must run before importing app modules ---
# An explicit empty value takes precedence over any value in backend/.env.
os.environ["GEMINI_API_KEY"] = ""
os.environ.setdefault("ENVIRONMENT", "test")
# Point the application's global engine at throwaway SQLite so an accidental
# query can never reach a real PostgreSQL instance.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

import contextlib
import socket
import uuid
from typing import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from unittest.mock import patch

import app.rag.document_processor as doc_proc
from app.core.database import Base
from app.api.deps import get_db
from app.main import app
from app.models.subject import Subject
from app.models.user import User
from app.rag.embeddings import embedding_service
from app.services.quiz_engine import quiz_generation_service
from app.rag.qa import rag_tutor_service

# In-memory SQLite async engine for tests
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    future=True
)

TestAsyncSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

# Mirrors the defaults in docker-compose.yml. Integration tests run inside a
# throwaway PostgreSQL *schema* rather than the public one, so pointing this at
# the development database cannot destroy real data.
DEFAULT_TEST_PG_URL = (
    "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_learning_assistant"
)


def test_postgres_url() -> str:
    """URL of the PostgreSQL/pgvector instance used by ``postgres`` tests."""
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_PG_URL)


def is_postgres_url(url: str) -> bool:
    return url.split("://", 1)[0].split("+", 1)[0].lower() in {"postgres", "postgresql"}


def requires_postgres(url: str) -> None:
    """Skip the calling test when no PostgreSQL/pgvector instance is configured."""
    if not is_postgres_url(url):
        pytest.skip(
            "TEST_DATABASE_URL is not a PostgreSQL URL; start the pgvector service "
            "(`docker compose up -d postgres`) and set TEST_DATABASE_URL to run "
            "pgvector integration tests."
        )


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Global AI / network guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="session")
def _offline_ai_services():
    """Forces every Gemini-backed service into deterministic offline mode.

    Production behaviour is untouched: this only flips the resolved key and the
    ``api_key_configured`` flags that the module-level singletons captured when
    they were constructed at import time.
    """
    from app.core.config import settings

    original_key = settings.GEMINI_API_KEY
    original_flags = (
        quiz_generation_service.api_key_configured,
        rag_tutor_service.api_key_configured,
    )
    settings.GEMINI_API_KEY = ""
    quiz_generation_service.api_key_configured = False
    rag_tutor_service.api_key_configured = False
    try:
        yield
    finally:
        settings.GEMINI_API_KEY = original_key
        quiz_generation_service.api_key_configured, rag_tutor_service.api_key_configured = original_flags


class _BlockedNetworkCall(RuntimeError):
    """Raised when a test tries to open a real network connection."""


# Captured at import time, before anything is patched, so the guard can hand the
# genuine implementations back to `socket.socketpair` below.
_REAL_SOCKET_CONNECT = socket.socket.connect
_REAL_SOCKET_CONNECT_EX = socket.socket.connect_ex
_REAL_SOCKETPAIR = socket.socketpair


def _loopback_socketpair(*args, **kwargs):
    """Creates a socket pair while the outbound-network guard stands down.

    ``socket.socketpair`` is a strictly local primitive: it only ever yields two
    already-connected descriptors and can never reach a remote host. It still has
    to be exempted, because on Windows CPython emulates it in pure Python using
    two real ``socket.connect()`` calls against a loopback address, and asyncio
    builds an event loop's self-pipe with it. Without this exemption the guard
    breaks event-loop construction and every async test errors during setup.
    """
    with patch.object(socket.socket, "connect", _REAL_SOCKET_CONNECT, create=True), patch.object(
        socket.socket, "connect_ex", _REAL_SOCKET_CONNECT_EX, create=True
    ):
        return _REAL_SOCKETPAIR(*args, **kwargs)


@pytest.fixture(autouse=True)
def _no_outbound_network(request: pytest.FixtureRequest) -> Iterator[None]:
    """Fails fast if SQLite-backed tests open a socket.

    The PostgreSQL/pgvector integration tests are exempt because they must talk
    to a real database over the loopback interface.
    """
    if "pg_engine" in request.fixturenames:
        yield
        return

    def _blocked(*args, **kwargs):
        raise _BlockedNetworkCall(
            "Outbound network access is disabled in tests. Stub the Gemini client "
            "or use the deterministic offline fallbacks instead."
        )

    targets = [
        ("socket", "create_connection"),
        ("socket", "getaddrinfo"),
    ]
    with contextlib.ExitStack() as stack:
        for module_name, attr in targets:
            stack.enter_context(
                patch(f"{module_name}.{attr}", side_effect=_blocked, create=True)
            )
        stack.enter_context(
            patch.object(socket.socket, "connect", side_effect=_blocked, create=True)
        )
        stack.enter_context(
            patch.object(socket.socket, "connect_ex", side_effect=_blocked, create=True)
        )
        stack.enter_context(
            patch("socket.socketpair", side_effect=_loopback_socketpair, create=True)
        )
        yield


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db(request: pytest.FixtureRequest):
    """Creates a fresh schema before every test and drops it afterwards.

    Opting into the ``pg_engine`` fixture hands schema lifecycle over to the
    PostgreSQL engine, so the SQLite work is skipped in that case.
    """
    if "pg_engine" in request.fixturenames:
        yield
        return

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _override_get_db():
        async with TestAsyncSessionLocal() as session:
            try:
                yield session
            finally:
                await session.close()

    app.dependency_overrides[get_db] = _override_get_db

    # Patch background task AsyncSessionLocal to use test database
    with patch.object(doc_proc, "AsyncSessionLocal", TestAsyncSessionLocal):
        yield
        app.dependency_overrides.clear()

        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def pg_engine() -> AsyncIterator:
    """Session factory bound to a live PostgreSQL + pgvector database.

    Used only by tests marked ``postgres``. Each test gets a freshly created,
    uniquely named schema, so runs are isolated from each other and from the
    application tables living in ``public``. Enable it with::

        docker compose up -d postgres
        TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/ai_learning_assistant \\
            python -m pytest -m postgres

    """
    url = test_postgres_url()
    requires_postgres(url)

    schema = f"test_{uuid.uuid4().hex[:12]}"
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        future=True,
        connect_args={"server_settings": {"search_path": f"public,{schema}"}},
    )
    try:
        async with engine.begin() as conn:
            # pgvector must exist before the Vector column type can be created.
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # pragma: no cover - depends on local environment
        await engine.dispose()
        pytest.skip(f"PostgreSQL/pgvector is not reachable at {url}: {exc}")

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    async def _override_get_db():
        async with session_factory() as session:
            try:
                yield session
            finally:
                await session.close()

    app.dependency_overrides[get_db] = _override_get_db
    with patch.object(doc_proc, "AsyncSessionLocal", session_factory):
        try:
            yield session_factory
        finally:
            app.dependency_overrides.clear()
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await engine.dispose()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Unauthenticated ASGI client bound to the test app."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def registered_user(client: AsyncClient) -> dict:
    """Registers a fresh student and returns ``{token, user_id, headers, client}``."""
    email = f"user-{uuid.uuid4().hex[:12]}@example.com"
    res = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "full_name": "Test Student", "password": "password123"},
    )
    assert res.status_code == 201, res.text
    data = res.json()
    token = data["access_token"]
    return {
        "email": email,
        "user_id": data["user"]["id"],
        "token": token,
        "headers": auth_headers(token),
    }


@pytest_asyncio.fixture
async def subject(registered_user: dict, client: AsyncClient) -> str:
    """An owned, active subject for ``registered_user``."""
    res = await client.post(
        "/api/v1/subjects",
        json={"title": "Retrieval Strategies", "description": "Vector search notes"},
        headers=registered_user["headers"],
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def seed_user_and_subject(
    session_factory,
    *,
    email: str | None = None,
    title: str = "Seeded Subject",
) -> tuple[str, str]:
    """Inserts a real user + active subject and returns ``(user_id, subject_id)``.

    PostgreSQL enforces the foreign keys that SQLite ignores, so tests that run
    against the real database must seed the parent rows too.
    """
    from app.models.subject import Subject
    from app.models.user import User

    email = email or f"seeded-{uuid.uuid4().hex[:12]}@example.com"
    async with session_factory() as session:
        user = User(
            email=email,
            full_name="Seeded Student",
            hashed_password="$pbkdf2-sha256$not-a-real-hash",
        )
        session.add(user)
        await session.flush()

        subject_row = Subject(user_id=user.id, title=title)
        session.add(subject_row)
        await session.commit()
        await session.refresh(subject_row)
        return str(user.id), str(subject_row.id)


async def seed_document_with_chunks(
    session_factory,
    *,
    user_id,
    subject_id,
    title: str = "Seed Notes.pdf",
    texts: "list[str]" = None,
    status: str = "READY",
) -> tuple[str, list[str]]:
    """Inserts a document plus vectorized chunks, returning ``(document_id, chunk_ids)``."""
    from app.models.document import Document
    from app.models.document_chunk import DocumentChunk

    texts = list(texts or ["Vector databases store embeddings for semantic search."])

    async with session_factory() as session:
        doc = Document(
            subject_id=uuid.UUID(str(subject_id)),
            user_id=uuid.UUID(str(user_id)),
            title=title,
            file_path="",
            file_size_bytes=1024,
            status=status,
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)

        chunk_ids = []
        for i, text_content in enumerate(texts, start=1):
            chunk = DocumentChunk(
                document_id=doc.id,
                subject_id=uuid.UUID(str(subject_id)),
                user_id=uuid.UUID(str(user_id)),
                chunk_index=i,
                page_number=i,
                token_count=max(1, len(text_content) // 4),
                content=text_content,
                embedding=await embedding_service.get_embedding(text_content),
            )
            session.add(chunk)
            await session.flush()
            chunk_ids.append(str(chunk.id))
        await session.commit()
        return str(doc.id), chunk_ids


@pytest_asyncio.fixture
async def seeded_chunks(registered_user: dict, subject: str):
    """Convenience wrapper seeding chunks through the active test session factory."""
    factory = TestAsyncSessionLocal

    async def _seed(texts, title: str = "Seed Notes.pdf", status: str = "READY"):
        return await seed_document_with_chunks(
            factory,
            user_id=registered_user["user_id"],
            subject_id=subject,
            title=title,
            texts=texts,
            status=status,
        )

    return _seed


@pytest_asyncio.fixture
async def owned_subject_id(registered_user: dict, subject: str) -> str:
    """Alias kept for readability in tests that only need an owned subject id."""
    return subject


@pytest_asyncio.fixture
async def other_user(client: AsyncClient) -> dict:
    """A second, unrelated authenticated user for cross-user access tests."""
    email = f"other-{uuid.uuid4().hex[:12]}@example.com"
    res = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "full_name": "Other Student", "password": "password123"},
    )
    assert res.status_code == 201, res.text
    data = res.json()
    token = data["access_token"]
    return {
        "email": email,
        "user_id": data["user"]["id"],
        "token": token,
        "headers": auth_headers(token),
    }
