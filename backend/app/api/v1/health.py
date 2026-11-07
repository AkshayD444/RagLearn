from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from app.core.config import settings
from app.core.database import check_database_connection

router = APIRouter(tags=["Health"])

# A3: the allow-list of fields that may leave the process. `/health/db` is
# unauthenticated -- it has to answer for a load balancer that holds no
# credentials -- so the response is built from a fixed set of known-safe keys
# rather than by passing the probe's dictionary straight through. Anything a
# future change adds to that dictionary (an error string, a DSN, a username)
# stays server-side by default.
PUBLIC_DB_HEALTH_FIELDS = ("status", "select_one", "pgvector_installed", "message")


def _public_db_health(db_status: dict) -> dict:
    """Copies only the fields that are safe to return to an anonymous caller."""
    return {key: db_status[key] for key in PUBLIC_DB_HEALTH_FIELDS if key in db_status}


@router.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    """Basic API health check endpoint."""
    return {
        "status": "online",
        "service": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT
    }


@router.get("/health/db")
async def db_health_check():
    """Database connectivity health check endpoint.

    Reports 503 when the database is unreachable so an uptime monitor treats a
    broken dependency as a failure. This used to answer 200 with the driver's
    own error text, which both hid the outage and disclosed the connection
    details to anyone who asked.
    """
    db_status = await check_database_connection()
    body = _public_db_health(db_status)

    if body.get("status") != "connected":
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "database": body},
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "healthy", "database": body},
    )
