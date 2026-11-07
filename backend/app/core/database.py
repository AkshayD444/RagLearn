from typing import AsyncGenerator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger("app.database")

# Create Async Engine for PostgreSQL
engine = create_async_engine(
    settings.DATABASE_URL,
    # Driven by SQL_ECHO, never by DEBUG. `echo=True` logs every statement
    # *and its bound parameters*, which on this schema means password hashes,
    # JWT subjects, quiz answers, document text and embedding vectors end up in
    # the application log. Opt in explicitly when debugging a query.
    echo=settings.SQL_ECHO,
    future=True,
    pool_pre_ping=True
)

# Async Session Factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for obtaining an async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def check_database_connection() -> dict:
    """Verifies connection to PostgreSQL database and checks if pgvector extension is available.

    A3: the failure branch used to return ``str(exc)`` to the caller, and the
    caller is an unauthenticated endpoint. Driver errors routinely embed the
    host, port, database name and username, so that response handed an anonymous
    caller the internal connection details. The exception is now logged
    server-side and the result carries a fixed message instead.
    """
    try:
        async with AsyncSessionLocal() as session:
            # Query 1: Basic connection check
            result = await session.execute(text("SELECT 1"))
            select_one = result.scalar()

            # Query 2: Check if pgvector extension is registered
            vector_check = await session.execute(
                text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
            )
            vector_installed = vector_check.scalar() is not None

            return {
                "status": "connected",
                "select_one": select_one == 1,
                "pgvector_installed": vector_installed,
                "message": "Database connection successful.",
            }
    except Exception:
        logger.exception("database_health_check_failed")
        return {
            "status": "disconnected",
            "select_one": False,
            "pgvector_installed": False,
            "message": "Database connection failed.",
        }
