from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.health import router as health_router
from app.api.v1.auth import router as auth_router
from app.api.v1.subjects import router as subjects_router
from app.api.v1.documents import router as documents_router
from app.api.v1.rag import router as rag_router
from app.api.v1.quizzes import router as quizzes_router
from app.api.v1.analytics import router as analytics_router
from app.api.v1.recommendations import router as recommendations_router
from app.api.v1.study_plans import router as study_plans_router
from app.api.v1.flashcards import router as flashcards_router
from app.core.config import settings
from app.core.database import engine
from app.core.logging_config import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.core.schema import ensure_schema

logger = get_logger("app.startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Configured before anything can log, so a schema or connection failure below
    # is itself observable.
    configure_logging()
    logger.info(
        "application_starting",
        extra={"environment": settings.ENVIRONMENT, "log_json": settings.LOG_JSON},
    )
    async with engine.begin() as conn:
        # Alembic owns the schema. This reconciles rather than creates, and
        # never alters or drops: see app/core/schema.py for the three cases.
        await conn.run_sync(ensure_schema)
    yield
    logger.info("application_stopping")


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
    lifespan=lifespan,
)

# CORS Configuration
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin) for origin in settings.BACKEND_CORS_ORIGINS],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Lets a browser surface our correlation id in its own error console.
        expose_headers=["X-Request-ID"],
    )

# Added after CORS so it is the outermost layer: every response, including one
# rejected by CORS, carries an X-Request-ID.
app.add_middleware(RequestContextMiddleware)

# Include API v1 Routers
app.include_router(health_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(subjects_router, prefix="/api/v1")
app.include_router(documents_router, prefix="/api/v1")
app.include_router(rag_router, prefix="/api/v1")
app.include_router(quizzes_router, prefix="/api/v1")
app.include_router(analytics_router, prefix="/api/v1")
app.include_router(recommendations_router, prefix="/api/v1")
app.include_router(study_plans_router, prefix="/api/v1")
app.include_router(flashcards_router, prefix="/api/v1")


@app.get("/")
async def root():
    return {
        "message": f"Welcome to {settings.PROJECT_NAME}",
        "docs_url": "/api/v1/docs",
        "health_check": "/api/v1/health",
    }
