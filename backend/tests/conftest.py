import pytest_asyncio
from unittest.mock import patch
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.database import Base
from app.api.deps import get_db
from app.main import app
import app.rag.document_processor as doc_proc

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


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    """Fixture to create fresh tables before each test and drop them after."""
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
