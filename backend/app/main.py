from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Optional

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

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
from app.core.auth_cookies import (
    ACCESS_COOKIE,
    UNSAFE_METHODS,
    csrf_rejection_response,
    csrf_token_matches,
)
from app.core.config import settings
from app.core.database import engine
from app.core.logging_config import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.core.rate_limit import describe_effective_limit_scope
from app.core.schema import ensure_schema
from app.rag.embeddings import EmbeddingError
from app.services.document_recovery import recover_orphaned_documents

logger = get_logger("app.startup")

# B3: the interactive documentation and the OpenAPI schema are the complete map
# of this API -- every route, every request model, every response shape. That is
# exactly what a local developer wants and exactly what should not be published
# from a public production host, where it hands an attacker the attack surface
# pre-mapped. Both URLs go to None together so the spec is not still reachable
# under /openapi.json once the Swagger UI is gone.
DEV_DOCS = ("/api/v1/docs", "/api/v1/redoc", "/api/v1/openapi.json")


@dataclass(frozen=True)
class DocsUrls:
    """The three documentation URLs, all-or-nothing."""

    docs: Optional[str]
    redoc: Optional[str]
    openapi: Optional[str]

    @property
    def enabled(self) -> bool:
        return self.docs is not None


def resolve_docs_urls(is_production: bool) -> DocsUrls:
    """Decides whether this deployment publishes its API surface.

    All three are switched together on purpose. Leaving ``openapi_url`` set while
    hiding the Swagger UI would not hide anything: /openapi.json is the machine
    readable copy of the same document and is the more convenient thing to
    scrape.
    """
    if is_production:
        return DocsUrls(docs=None, redoc=None, openapi=None)
    return DocsUrls(docs=DEV_DOCS[0], redoc=DEV_DOCS[1], openapi=DEV_DOCS[2])


_DOCS_URLS = resolve_docs_urls(settings.is_production)
DOCS_AVAILABLE = _DOCS_URLS.enabled
DOCS_URL = _DOCS_URLS.docs
REDOC_URL = _DOCS_URLS.redoc
OPENAPI_URL = _DOCS_URLS.openapi


class CsrfProtectionMiddleware(BaseHTTPMiddleware):
    """B2: guards cookie-authenticated writes against cross-site requests.

    Now that the access token rides in a cookie the browser attaches
    automatically, a page on another origin can cause a state-changing request
    to this API without ever seeing the token -- classic CSRF. The check is here,
    in one middleware, rather than as a dependency on the ~40 state-changing
    routes, so a route added later cannot forget it.

    Two requests are deliberately not subject to it:

    * anything without the access cookie, which is how a request with no session
      at all behaves today;
    * anything carrying an ``Authorization`` header. That credential was supplied
      by the caller rather than attached by the browser, so there is nothing for
      another site to ride on. This is what keeps Swagger's OAuth2 flow, curl and
      mobile clients working with no changes.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if (
            settings.CSRF_PROTECTION_ENABLED
            and request.method.upper() in UNSAFE_METHODS
            and request.cookies.get(ACCESS_COOKIE)
            and not request.headers.get("Authorization")
            and not csrf_token_matches(request)
        ):
            logger.warning(
                "csrf_rejected",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            return csrf_rejection_response()
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Configured before anything can log, so a schema or connection failure below
    # is itself observable.
    configure_logging()
    logger.info(
        "application_starting",
        extra={"environment": settings.ENVIRONMENT, "log_json": settings.LOG_JSON},
    )
    # After configure_logging, so it lands in the configured format and stream.
    describe_effective_limit_scope()
    async with engine.begin() as conn:
        # Alembic owns the schema. This reconciles rather than creates, and
        # never alters or drops: see app/core/schema.py for the three cases.
        await conn.run_sync(ensure_schema)
    # B6: ingestion runs as an in-process background task, so a restart, a
    # redeploy or an OOM kill mid-PDF leaves its document stuck in PROCESSING
    # with no worker that still intends to finish it -- and reprocess refuses it
    # with 409, so the owner could not retry. This sweep runs before the app
    # serves a single request, when no ingestion task can be in flight here, and
    # moves those rows to FAILED with a reason the owner can see and retry from.
    # It logs its own outcome. Deliberately not fatal: an interrupted document
    # must not stop the API from starting, so a failure here is logged and the
    # rows are left for the next start rather than taking the deployment down.
    try:
        await recover_orphaned_documents()
    except Exception:
        logger.exception("orphaned_document_recovery_failed")
    yield
    logger.info("application_stopping")


app = FastAPI(
    title=settings.PROJECT_NAME,
    # B3: all three are None in production, which makes FastAPI return 404 for
    # the docs routes and for the schema itself, so /openapi.json cannot be used
    # to get at what /docs would have rendered.
    openapi_url=OPENAPI_URL,
    docs_url=DOCS_URL,
    redoc_url=REDOC_URL,
    # A2: DEBUG is FastAPI's own switch (it returns tracebacks in 500 bodies).
    # It is no longer the input to SQLAlchemy's `echo`, which logs bound
    # parameters -- see app/core/database.py. Defaults to False.
    debug=settings.DEBUG,
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

# B2. Inside RequestContextMiddleware so a CSRF rejection is still correlated and
# logged like any other response; outside CORS is not a concern here because a
# preflight is a safe method and never reaches this check.
app.add_middleware(CsrfProtectionMiddleware)

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


# B2: the rate-limit counters live in this process's memory, so the limit a
# caller actually faces depends on how many workers are running. The app is
# deployed with one, where the configured limits are exact. lifespan() says so at
# startup -- and says so loudly if the declared worker count is above one -- so a
# weakened limit is something an operator reads in the logs rather than
# something they have to infer from the source.


@app.exception_handler(EmbeddingError)
async def embedding_error_handler(
    request: Request, exc: EmbeddingError
) -> JSONResponse:
    """B1: turns an embedding failure into an honest 503.

    Retrieval, quiz generation, flashcard generation and the tutor all embed the
    user's query, so each reaches this when Gemini is unconfigured or failing.
    Letting the error escape produced a 500, which reads as a bug in the
    endpoint rather than a dependency being down, and it did so on every request
    for as long as the outage lasted.

    The client gets a fixed message and nothing else: these exceptions name the
    missing setting and the failing model, which is useful in a log and
    pointless -- and mildly informative -- to hand back. The detail, including
    the correlation id, goes to the log. 503 rather than 400 because nothing
    about the request is wrong and a retry is the correct client behaviour.
    """
    logger.error(
        "embedding_unavailable",
        extra={
            "path": request.url.path,
            "method": request.method,
            "error_type": type(exc).__name__,
        },
    )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "detail": (
                "Semantic search is temporarily unavailable because the "
                "embedding service could not be reached. Please try again "
                "shortly."
            )
        },
    )


@app.get("/")
async def root():
    return {
        "message": f"Welcome to {settings.PROJECT_NAME}",
        # Not a hardcoded string any more: advertising a docs URL that 404s is
        # worse than not mentioning it. B3.
        "docs_url": DOCS_URL,
        "health_check": "/api/v1/health",
    }
