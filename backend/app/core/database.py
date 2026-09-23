from typing import AsyncGenerator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# Create Async Engine for PostgreSQL
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
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
    """Verifies connection to PostgreSQL database and checks if pgvector extension is available."""
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
                "message": "Database connection successful."
            }
    except Exception as e:
        return {
            "status": "disconnected",
            "select_one": False,
            "pgvector_installed": False,
            "error": str(e)
        }
